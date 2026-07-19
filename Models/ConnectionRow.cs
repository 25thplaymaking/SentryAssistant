using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Sentry.Contracts;

namespace SentryAssistant.Models;

/// <summary>
/// One row on the connections page.
///
/// The remedy is shown only when there is one, so a working connection does not
/// carry an empty line where advice would be.
/// </summary>
public sealed class ConnectionRow
{
    public ConnectionRow(ConnectionDescriptor connection)
    {
        Name = connection.Name;
        Category = connection.Category;
        Label = connection.Label;
        Detail = connection.Detail;
        Remedy = connection.Remedy;
        RemedyVisibility = connection.HasRemedy ? Visibility.Visible : Visibility.Collapsed;
        ToneBrush = ToneBrushes.For(connection.Tone);

        // A few words for the narrow inspector rail, where the full detail would
        // wrap to four lines or be trimmed into nonsense.
        ShortState = connection.State switch
        {
            IntegrationState.Connected => "ready",
            IntegrationState.Detected => "local, subscription",
            IntegrationState.Missing => "not installed",
            IntegrationState.Misconfigured => "needs fixing",
            _ => "not applicable"
        };

        // One string carrying name, state, and reason: a screen reader should not
        // have to piece the row together from three separate stops.
        AutomationName = connection.HasRemedy
            ? $"{connection.Name}, {connection.Category}. {connection.Label}. "
              + $"{connection.Detail} Remedy: {connection.Remedy}"
            : $"{connection.Name}, {connection.Category}. {connection.Label}. {connection.Detail}";
    }

    public string ShortState { get; }

    public string Name { get; }
    public string Category { get; }
    public string Label { get; }
    public string Detail { get; }
    public string Remedy { get; }
    public Visibility RemedyVisibility { get; }
    public Brush ToneBrush { get; }
    public string AutomationName { get; }
}
