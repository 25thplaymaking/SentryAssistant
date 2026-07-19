using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Sentry.Contracts;

namespace SentryAssistant.Models;

/// <summary>
/// View rows for the Hermes control centre.
///
/// Brushes are resolved here rather than in XAML because these are bound from
/// data. They still come from the shared theme keys, so light, dark, and high
/// contrast follow the rest of the shell.
/// </summary>
internal static class ToneBrushes
{
    public static Brush For(PresenceTone tone)
    {
        var key = tone switch
        {
            PresenceTone.Positive => "SentrySuccessBrush",
            PresenceTone.Pending => "SentryWarningBrush",
            PresenceTone.Attention => "SentryInfoBrush",
            PresenceTone.Critical => "SentryDangerBrush",
            _ => "SentryTextMutedBrush"
        };
        return (Brush)Application.Current.Resources[key];
    }
}

public sealed class FindingRow
{
    public FindingRow(AdminFinding finding)
    {
        SeverityLabel = finding.Severity.ToUpperInvariant();
        Summary = finding.Summary;
        Remedy = finding.Remedy;
        RemedyVisibility = finding.HasRemedy ? Visibility.Visible : Visibility.Collapsed;
        ToneBrush = ToneBrushes.For(finding.Tone);
    }

    public string SeverityLabel { get; }
    public string Summary { get; }
    public string Remedy { get; }
    public Visibility RemedyVisibility { get; }
    public Brush ToneBrush { get; }
}

public sealed class CapabilityRow
{
    public CapabilityRow(string name, bool supported)
    {
        Name = name;
        Label = supported ? "YES" : "NO";
        // An unsupported capability is information, not a fault, so it stays
        // neutral rather than reading as an error.
        ToneBrush = ToneBrushes.For(supported ? PresenceTone.Positive : PresenceTone.Neutral);
    }

    public string Name { get; }
    public string Label { get; }
    public Brush ToneBrush { get; }
}
