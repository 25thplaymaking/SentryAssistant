using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Sentry.Contracts;
using Windows.UI.ViewManagement;

namespace SentryAssistant.Controls;

/// <summary>
/// Renders one <see cref="SentryLifecycleState"/>. All presentation policy comes
/// from the unit-tested <see cref="SentryPresence"/> descriptor; this control only
/// maps that descriptor onto visual states and the accessibility tree.
/// </summary>
public sealed partial class SentryPresenceView : UserControl
{
    // UISettings must be kept alive for its change event to keep firing.
    private readonly UISettings _uiSettings = new();

    public SentryPresenceView()
    {
        InitializeComponent();

        // Live change notification for the animation setting arrived in Windows 10 2004.
        // The app still supports 17763, where the setting is read once at load instead.
        // The version check is inline because that is the form the platform analyzer understands.
        Loaded += (_, _) =>
        {
            if (OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
            {
                _uiSettings.AnimationsEnabledChanged += OnSystemAnimationsChanged;
            }

            Apply();
        };

        Unloaded += (_, _) =>
        {
            if (OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
            {
                _uiSettings.AnimationsEnabledChanged -= OnSystemAnimationsChanged;
            }
        };
    }

    public static readonly DependencyProperty StateProperty = DependencyProperty.Register(
        nameof(State),
        typeof(SentryLifecycleState),
        typeof(SentryPresenceView),
        new PropertyMetadata(SentryLifecycleState.Idle, OnStateChanged));

    public SentryLifecycleState State
    {
        get => (SentryLifecycleState)GetValue(StateProperty);
        set => SetValue(StateProperty, value);
    }

    /// <summary>The descriptor currently being rendered, after reduced motion is applied.</summary>
    public PresenceDescriptor Descriptor { get; private set; } =
        SentryPresence.Describe(SentryLifecycleState.Idle);

    private static void OnStateChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) =>
        ((SentryPresenceView)sender).Apply();

    private void OnSystemAnimationsChanged(UISettings sender, object args) =>
        DispatcherQueue.TryEnqueue(Apply);

    private void Apply()
    {
        // Honour the Windows "animation effects" setting. The descriptor drops its
        // motion but keeps tone, label, and announcement, so the state is still clear.
        var descriptor = SentryPresence.Describe(State)
            .WithMotionAllowed(_uiSettings.AnimationsEnabled);

        Descriptor = descriptor;

        VisualStateManager.GoToState(this, ToneStateName(descriptor.Tone), useTransitions: false);
        VisualStateManager.GoToState(this, MotionStateName(descriptor.Motion), useTransitions: true);

        // Screen readers announce the state change rather than a bare colour swap.
        AutomationProperties.SetName(this, descriptor.Announcement);
        ToolTipService.SetToolTip(this, $"{descriptor.Label} — {descriptor.Detail}");
    }

    private static string ToneStateName(PresenceTone tone) => tone switch
    {
        PresenceTone.Neutral => "ToneNeutral",
        PresenceTone.Pending => "TonePending",
        PresenceTone.Attention => "ToneAttention",
        PresenceTone.Positive => "TonePositive",
        PresenceTone.Critical => "ToneCritical",
        _ => "ToneNeutral"
    };

    private static string MotionStateName(PresenceMotion motion) => motion switch
    {
        PresenceMotion.Continuous => "MotionContinuous",
        PresenceMotion.Completion => "MotionCompletion",
        _ => "MotionNone"
    };
}
