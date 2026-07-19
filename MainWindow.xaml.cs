using Microsoft.UI;
using Microsoft.UI.Xaml;
using Windows.UI;

namespace SentryAssistant;

/// <summary>
/// The application window. This hosts a Frame that displays pages. Add your
/// UI and logic to MainPage.xaml / MainPage.xaml.cs instead of here so you
/// can use Page features such as navigation events and the Loaded lifecycle.
/// </summary>
public sealed partial class MainWindow : Window
{
    public static MainWindow Instance { get; private set; } = null!;

    public MainWindow()
    {
        Instance = this;
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);

        AppWindow.SetIcon("Assets/AppIcon.ico");
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1360, 860));

        // Caption buttons are drawn by the system, so they do not inherit the XAML
        // theme. Without this they keep dark-theme colours and become nearly
        // invisible against a light title bar.
        if (Content is FrameworkElement root)
        {
            root.ActualThemeChanged += (_, _) => ApplyCaptionButtonTheme(root.ActualTheme);
            ApplyCaptionButtonTheme(root.ActualTheme);
        }

        RootFrame.Navigate(typeof(MainPage));
    }

    private void ApplyCaptionButtonTheme(ElementTheme theme)
    {
        var titleBar = AppWindow.TitleBar;
        var light = theme == ElementTheme.Light;

        var foreground = light ? Color.FromArgb(255, 16, 18, 16) : Color.FromArgb(255, 242, 244, 240);
        var inactive = light ? Color.FromArgb(140, 16, 18, 16) : Color.FromArgb(140, 242, 244, 240);
        var hover = light ? Color.FromArgb(20, 13, 15, 13) : Color.FromArgb(28, 255, 255, 255);
        var pressed = light ? Color.FromArgb(38, 13, 15, 13) : Color.FromArgb(46, 255, 255, 255);

        // Transparent backgrounds let the custom title bar surface show through.
        titleBar.ButtonBackgroundColor = Colors.Transparent;
        titleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
        titleBar.ButtonForegroundColor = foreground;
        titleBar.ButtonInactiveForegroundColor = inactive;
        titleBar.ButtonHoverForegroundColor = foreground;
        titleBar.ButtonPressedForegroundColor = foreground;
        titleBar.ButtonHoverBackgroundColor = hover;
        titleBar.ButtonPressedBackgroundColor = pressed;
    }
}
