using System.Diagnostics;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace SentryShell;

/// <summary>
/// Frontir Sentry desktop shell: one window, the Sentry UI inside it, the SSH
/// forward owned in-process. No browser tabs, no console, no PowerShell.
/// </summary>
internal static class Program
{
    // Matches --fs-canvas / the PWA theme_color, so the window never flashes
    // white before the UI paints.
    private static readonly Color Canvas = Color.FromArgb(0x09, 0x09, 0x0B);
    private static readonly Color Bone   = Color.FromArgb(0xF4, 0xF3, 0xEF);
    private static readonly Color Muted  = Color.FromArgb(0xA3, 0xA3, 0xAB);

    // Set only by the tray's Quit item. FormClosing consults it to tell a real
    // exit apart from the user pressing X, which must not end the session.
    private static bool _exiting;

    // The "it's still running" balloon is shown once per launch, not once per
    // hide — repeating it on every close would be nagware.
    private static bool _toldAboutTray;

    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();

        // Provision the app's own areas BEFORE anything reaches for them. On a
        // fresh end-user machine none of these exist, and the pieces that need
        // them (the tunnel's log, the WebView2 profile) would otherwise each
        // create-on-demand in a different code path — so a failure surfaced as
        // a confusing downstream error instead of one clear message. Do it once,
        // up front, and refuse to run blind if the root cannot be made.
        try
        {
            AppPaths.EnsureAll();
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                $"Frontir Sentry could not create its data folder:\n\n{AppPaths.Root}\n\n{ex.Message}",
                "Frontir Sentry", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        var tunnel = new Tunnel();
        var form = BuildWindow(tunnel, out var web, out var status);
        // Disposed when Main returns, which is what removes the icon. Without
        // it Windows leaves a ghost in the tray until the user hovers over it.
        using var tray = BuildTray(form, tunnel, web, status);

        form.Shown += async (_, _) =>
        {
            var progress = new Progress<string>(s => status.Text = s);
            var ok = await tunnel.StartAsync(TimeSpan.FromSeconds(45), progress);
            if (!ok)
            {
                status.Text = "Could not reach grain.silo. Check VPN/network, then use Reconnect.";
                return;
            }

            status.Text = "Loading Sentry…";
            try
            {
                // Persist cookies/localStorage across launches so the login and
                // the chosen skin survive a restart. Provisioned at startup by
                // AppPaths.EnsureAll(); create again here only to be safe if it
                // was cleared while running.
                Directory.CreateDirectory(AppPaths.WebView2);
                var env = await CoreWebView2Environment.CreateAsync(null, AppPaths.WebView2);
                await web.EnsureCoreWebView2Async(env);

                var core = web.CoreWebView2;
                core.Settings.AreDefaultContextMenusEnabled = true;
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.AreBrowserAcceleratorKeysEnabled = false;  // no Ctrl+T/Ctrl+N spawning tabs
                core.Settings.IsSwipeNavigationEnabled = false;

                // Anything that would open a new window (target=_blank, a share
                // link) goes to the real browser instead of spawning a second
                // chrome-less shell the user cannot navigate.
                core.NewWindowRequested += (_, e) =>
                {
                    e.Handled = true;
                    try { Process.Start(new ProcessStartInfo(e.Uri) { UseShellExecute = true }); }
                    catch { /* nothing sensible to do if no browser is registered */ }
                };

                core.NavigationCompleted += (_, e) =>
                {
                    status.Visible = !e.IsSuccess;
                    if (!e.IsSuccess) status.Text = $"Failed to load ({e.WebErrorStatus}). Use Reconnect.";
                    else web.Visible = true;
                };

                core.DocumentTitleChanged += (_, _) =>
                    form.Text = string.IsNullOrWhiteSpace(core.DocumentTitle)
                        ? "Frontir Sentry" : $"{core.DocumentTitle} — Frontir Sentry";

                web.Source = new Uri(Tunnel.WebUiUrl);
            }
            catch (Exception ex)
            {
                tunnel.Log($"webview init failed: {ex}");
                status.Text = "WebView2 failed to start. Is the Edge WebView2 Runtime installed?";
            }
        };

        // X hides to the tray instead of ending the session. Sentry is a
        // background presence, not a document window: the tunnel is owned
        // in-process, so a real exit drops the forward and makes the next
        // launch pay the full dial again. Quit from the tray menu is the way
        // out, and CloseReason keeps Windows shutdown/logoff from being
        // mistaken for it — cancelling those would block the shutdown.
        form.FormClosing += (_, e) =>
        {
            if (_exiting || e.CloseReason != CloseReason.UserClosing) return;
            e.Cancel = true;
            HideToTray(form, tray);
        };

        form.FormClosed += (_, _) => tunnel.Dispose();

        Application.Run(form);
    }

    /// <summary>Reconnect the forward and reload, shared by both menus.</summary>
    private static async Task ReconnectAsync(Tunnel tunnel, WebView2 web, Label status)
    {
        status.Text = "Reconnecting…";
        status.Visible = true;
        web.Visible = false;
        if (await tunnel.StartAsync(TimeSpan.FromSeconds(45)))
            web.CoreWebView2?.Navigate(Tunnel.WebUiUrl);
        else
            status.Text = "Still cannot reach grain.silo.";
    }

    private static void OpenLogFolder()
    {
        try { Process.Start(new ProcessStartInfo(AppPaths.Root) { UseShellExecute = true }); } catch { }
    }

    private static void RestoreFromTray(Form form)
    {
        form.Show();
        if (form.WindowState == FormWindowState.Minimized) form.WindowState = FormWindowState.Normal;
        form.Activate();
    }

    private static void HideToTray(Form form, NotifyIcon tray)
    {
        form.Hide();
        if (_toldAboutTray) return;
        _toldAboutTray = true;
        // Closing to a tray icon is invisible if the user does not know it
        // happened — the first time, say so once.
        try
        {
            tray.ShowBalloonTip(
                4000,
                "Sentry is still running",
                "The window closed to the system tray. Use Quit there to stop it.",
                ToolTipIcon.None);
        }
        catch { /* balloons are suppressible by policy; never fatal */ }
    }

    /// <summary>The tray icon and its menu — the only route to a real exit.</summary>
    private static NotifyIcon BuildTray(Form form, Tunnel tunnel, WebView2 web, Label status)
    {
        var menu = new ContextMenuStrip();

        var open = new ToolStripMenuItem("Open Sentry");
        open.Click += (_, _) => RestoreFromTray(form);

        var reconnect = new ToolStripMenuItem("Reconnect");
        reconnect.Click += async (_, _) => await ReconnectAsync(tunnel, web, status);

        var logs = new ToolStripMenuItem("Open log folder");
        logs.Click += (_, _) => OpenLogFolder();

        var quit = new ToolStripMenuItem("Quit Sentry");
        quit.Click += (_, _) =>
        {
            _exiting = true;
            Application.Exit();   // closes the form, which disposes the tunnel
        };

        menu.Items.Add(open);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(reconnect);
        menu.Items.Add(logs);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(quit);

        var tray = new NotifyIcon
        {
            Text = "Frontir Sentry",
            Icon = TrayIcon(),
            ContextMenuStrip = menu,
            Visible = true,
        };
        // Double-click, not single: a single click belongs to the context menu
        // on the right button and selection on the left everywhere in Windows.
        tray.DoubleClick += (_, _) => RestoreFromTray(form);
        return tray;
    }

    private static Icon TrayIcon()
    {
        // frontir.ico is an <ApplicationIcon>, i.e. embedded in the PE — it is
        // NOT copied next to the exe, and under PublishSingleFile there is no
        // loose file to find at all. Reading it off the executable is what
        // actually yields the shield here; the loose-file probe is only a
        // courtesy for a plain `dotnet run` from the project directory.
        try
        {
            var ico = Path.Combine(AppContext.BaseDirectory, "frontir.ico");
            // Ask for the small-icon size so Windows picks that frame out of
            // the .ico rather than downscaling the 256px one into mush.
            if (File.Exists(ico)) return new Icon(ico, SystemInformation.SmallIconSize);
        }
        catch { /* fall through */ }
        try
        {
            var self = Environment.ProcessPath;
            if (!string.IsNullOrEmpty(self))
            {
                var embedded = Icon.ExtractAssociatedIcon(self);
                if (embedded is not null) return embedded;
            }
        }
        catch { /* fall through to the stock icon */ }
        return SystemIcons.Application;
    }

    private static Form BuildWindow(Tunnel tunnel, out WebView2 web, out Label status)
    {
        var form = new Form
        {
            Text = "Frontir Sentry",
            BackColor = Canvas,
            ClientSize = new Size(1360, 900),
            MinimumSize = new Size(760, 560),
            StartPosition = FormStartPosition.CenterScreen,
        };
        try
        {
            var ico = Path.Combine(AppContext.BaseDirectory, "frontir.ico");
            if (File.Exists(ico)) form.Icon = new Icon(ico);
        }
        catch { /* default icon is survivable */ }

        // Hidden until first paint so the user never sees WebView2's white flash.
        web = new WebView2
        {
            Dock = DockStyle.Fill,
            DefaultBackgroundColor = Canvas,
            Visible = false,
        };

        status = new Label
        {
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleCenter,
            ForeColor = Muted,
            BackColor = Canvas,
            Font = new Font("Segoe UI", 10.5f),
            Text = "Starting…",
        };

        // Reconnect lives in the system menu rather than a toolbar: the UI is
        // the product, and chrome around it would undo the point of the shell.
        var menu = new ContextMenuStrip();
        var reconnect = new ToolStripMenuItem("Reconnect");
        var localWeb = web;
        var localStatus = status;
        reconnect.Click += async (_, _) => await ReconnectAsync(tunnel, localWeb, localStatus);
        var logs = new ToolStripMenuItem("Open log folder");
        logs.Click += (_, _) => OpenLogFolder();
        menu.Items.Add(reconnect);
        menu.Items.Add(logs);
        status.ContextMenuStrip = menu;
        form.ContextMenuStrip = menu;

        form.Controls.Add(web);
        form.Controls.Add(status);
        status.BringToFront();
        return form;
    }
}
