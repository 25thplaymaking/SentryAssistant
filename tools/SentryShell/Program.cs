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

    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();

        var tunnel = new Tunnel();
        var form = BuildWindow(tunnel, out var web, out var status);

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
                // the chosen skin survive a restart.
                var dataDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "SentryAssistant", "webview2");
                Directory.CreateDirectory(dataDir);
                var env = await CoreWebView2Environment.CreateAsync(null, dataDir);
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

        form.FormClosed += (_, _) => tunnel.Dispose();

        Application.Run(form);
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
        reconnect.Click += async (_, _) =>
        {
            localStatus.Text = "Reconnecting…";
            localStatus.Visible = true;
            localWeb.Visible = false;
            if (await tunnel.StartAsync(TimeSpan.FromSeconds(45)))
                localWeb.CoreWebView2?.Navigate(Tunnel.WebUiUrl);
            else
                localStatus.Text = "Still cannot reach grain.silo.";
        };
        var logs = new ToolStripMenuItem("Open log folder");
        logs.Click += (_, _) =>
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SentryAssistant");
            try { Process.Start(new ProcessStartInfo(dir) { UseShellExecute = true }); } catch { }
        };
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
