using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SentryAssistant.Models;

namespace SentryAssistant.Controls;

/// <summary>
/// Picks a distinct template per author so a user turn and a Sentry turn are
/// never mistaken for each other. Keeping this as templates rather than computed
/// brushes leaves every colour a ThemeResource.
/// </summary>
public sealed partial class MessageTemplateSelector : DataTemplateSelector
{
    public DataTemplate? UserTemplate { get; set; }
    public DataTemplate? SentryTemplate { get; set; }
    public DataTemplate? SystemTemplate { get; set; }

    protected override DataTemplate? SelectTemplateCore(object item) =>
        SelectTemplateCore(item, null);

    protected override DataTemplate? SelectTemplateCore(object item, DependencyObject? container) =>
        (item as AssistantMessage)?.Author switch
        {
            MessageAuthor.User => UserTemplate ?? SentryTemplate,
            MessageAuthor.System => SystemTemplate ?? SentryTemplate,
            _ => SentryTemplate
        };
}
