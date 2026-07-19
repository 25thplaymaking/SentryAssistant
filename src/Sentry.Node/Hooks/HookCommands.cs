using System.Text.Json;

namespace Sentry.Node.Hooks;

/// <summary>
/// The <c>install-hooks</c>, <c>uninstall-hooks</c> and <c>hook</c> verbs.
///
/// The merge itself lives in <see cref="SentryHookInstaller"/> and is tested
/// there. This is the file handling around it: back up first, write once, and
/// never touch a settings file that could not be parsed.
/// </summary>
public static class HookCommands
{
    public static string DefaultSettingsPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        ".claude", "settings.json");

    /// <summary>Where sanitised observations are appended.</summary>
    public static string DefaultActivityPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SentryAssistant", "harness-activity.jsonl");

    public static int Run(string[] args, TextWriter output, TextReader input)
    {
        var verb = args[0];
        var settingsPath = Option(args, "--settings") ?? DefaultSettingsPath;

        return verb switch
        {
            "install-hooks" => Install(settingsPath, Option(args, "--command"), output),
            "uninstall-hooks" => Uninstall(settingsPath, output),
            "hook" => Record(input, output),
            _ => Unknown(verb, output)
        };
    }

    private static int Install(string settingsPath, string? command, TextWriter output)
    {
        command ??= $"\"{Environment.ProcessPath}\" hook";

        if (!File.Exists(settingsPath))
        {
            output.WriteLine($"No settings file at {settingsPath}.");
            return 2;
        }

        var original = File.ReadAllText(settingsPath);

        string merged;
        try
        {
            merged = SentryHookInstaller.Install(original, command);
        }
        catch (Exception exception)
        {
            // A settings file that cannot be understood is left exactly as it is.
            // Overwriting it with something well-formed would lose the original.
            output.WriteLine($"Refusing to modify {settingsPath}: {exception.Message}");
            return 2;
        }

        if (merged == original)
        {
            output.WriteLine("Sentry hooks are already installed. Nothing changed.");
            return 0;
        }

        var backup = Backup(settingsPath, original);
        File.WriteAllText(settingsPath, merged);

        output.WriteLine($"Backed up to {backup}");
        output.WriteLine($"Installed Sentry hooks on {SentryHookInstaller.Events.Count} events.");
        output.WriteLine("Existing hooks were left in place.");
        return 0;
    }

    private static int Uninstall(string settingsPath, TextWriter output)
    {
        if (!File.Exists(settingsPath))
        {
            output.WriteLine($"No settings file at {settingsPath}.");
            return 2;
        }

        var original = File.ReadAllText(settingsPath);
        if (!SentryHookInstaller.IsInstalled(original))
        {
            output.WriteLine("Sentry hooks are not installed. Nothing changed.");
            return 0;
        }

        var reverted = SentryHookInstaller.Uninstall(original);
        var backup = Backup(settingsPath, original);
        File.WriteAllText(settingsPath, reverted);

        output.WriteLine($"Backed up to {backup}");
        output.WriteLine("Removed Sentry's hooks. Other hooks were left in place.");
        return 0;
    }

    /// <summary>
    /// Record one event. Always succeeds from the harness's point of view: a
    /// hook that fails is a hook that interrupts someone's session.
    /// </summary>
    private static int Record(TextReader input, TextWriter output)
    {
        try
        {
            var observation = SentryHookReceiver.Observe(input.ReadToEnd());
            if (observation is null) return 0;

            var path = DefaultActivityPath;
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.AppendAllText(path, JsonSerializer.Serialize(observation) + Environment.NewLine);
        }
        catch (Exception)
        {
            // Deliberately swallowed. Sentry's bookkeeping must never be the
            // reason a harness run stops.
        }

        return 0;
    }

    private static string Backup(string settingsPath, string contents)
    {
        var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss");
        var backup = $"{settingsPath}.sentry-{stamp}.bak";
        File.WriteAllText(backup, contents);
        return backup;
    }

    private static int Unknown(string verb, TextWriter output)
    {
        output.WriteLine($"Unknown command '{verb}'.");
        return 2;
    }

    private static string? Option(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }
}
