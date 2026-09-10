using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Sentry.Node.Workspaces;

/// <summary>
/// CLI handler for workspace list, workspace add, and workspace remove.
/// Modifies appsettings.node.json locally on the workstation so the node file watcher
/// and re-registration publish it to the Gateway immediately.
/// </summary>
public static class WorkspaceCommands
{
    private static readonly HashSet<string> BlockedRoots = new(StringComparer.OrdinalIgnoreCase)
    {
        Path.GetPathRoot(Environment.SystemDirectory) ?? @"C:\",
        Environment.GetFolderPath(Environment.SpecialFolder.Windows),
        Environment.GetFolderPath(Environment.SpecialFolder.System),
        Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
        Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86)
    };

    public static int Run(string[] args, string configPath, TextWriter output, TextWriter error)
    {
        if (args.Length < 2)
        {
            PrintUsage(output);
            return 1;
        }

        var subVerb = args[1].ToLowerInvariant();
        return subVerb switch
        {
            "list" => List(configPath, output, error),
            "add" => Add(args, configPath, output, error),
            "remove" => Remove(args, configPath, output, error),
            _ => Unknown(subVerb, output)
        };
    }

    private static int List(string configPath, TextWriter output, TextWriter error)
    {
        if (!File.Exists(configPath))
        {
            error.WriteLine($"Configuration not found at {configPath}");
            return 2;
        }

        try
        {
            var text = File.ReadAllText(configPath);
            var doc = JsonNode.Parse(text);
            var workspaces = doc?["workspaces"]?.AsArray();

            if (workspaces is null || workspaces.Count == 0)
            {
                output.WriteLine("No workspaces registered on this node.");
                return 0;
            }

            output.WriteLine($"Workspaces registered in {configPath}:");
            output.WriteLine(new string('-', 75));
            output.WriteLine(string.Format("{0,-20} {1,-35} {2}", "Workspace ID", "Root Path", "Modes"));
            output.WriteLine(new string('-', 75));

            foreach (var node in workspaces)
            {
                if (node is null) continue;
                var id = (string?)node["workspaceId"] ?? "<unknown>";
                var path = (string?)node["rootPath"] ?? "<unknown>";
                var modes = string.Join(", ", node["allowedModes"]?.AsArray().Select(m => (string?)m) ?? []);
                output.WriteLine(string.Format("{0,-20} {1,-35} {2}", id, path, modes));
            }
            return 0;
        }
        catch (Exception ex)
        {
            error.WriteLine($"Error reading workspaces: {ex.Message}");
            return 2;
        }
    }

    private static int Add(string[] args, string configPath, TextWriter output, TextWriter error)
    {
        if (args.Length < 3)
        {
            error.WriteLine("Usage: sentry-node workspace add <path> [--id <workspaceId>] [--mode readOnly|workspaceWrite]");
            return 1;
        }

        var rawPath = args[2].Trim('"', '\'');
        if (string.IsNullOrWhiteSpace(rawPath))
        {
            error.WriteLine("Path cannot be empty.");
            return 1;
        }

        string fullPath;
        try
        {
            fullPath = Path.GetFullPath(rawPath);
        }
        catch (Exception ex)
        {
            error.WriteLine($"Invalid path format: {ex.Message}");
            return 1;
        }

        if (!Directory.Exists(fullPath))
        {
            error.WriteLine($"Directory does not exist: {fullPath}");
            return 1;
        }

        foreach (var blocked in BlockedRoots)
        {
            if (string.Equals(fullPath, blocked, StringComparison.OrdinalIgnoreCase))
            {
                error.WriteLine($"Refusing to register system directory '{fullPath}' as a workspace.");
                return 1;
            }
        }

        string? customId = null;
        var mode = "workspaceWrite";

        for (var i = 3; i < args.Length; i++)
        {
            if (string.Equals(args[i], "--id", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
            {
                customId = args[++i].Trim();
            }
            else if (string.Equals(args[i], "--mode", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
            {
                mode = args[++i].Trim();
            }
        }

        var workspaceId = !string.IsNullOrWhiteSpace(customId)
            ? Slugify(customId)
            : Slugify(Path.GetFileName(fullPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)));

        if (string.IsNullOrWhiteSpace(workspaceId))
        {
            error.WriteLine("Could not determine a valid workspace ID from the path. Specify --id <id> explicitly.");
            return 1;
        }

        if (!File.Exists(configPath))
        {
            error.WriteLine($"Configuration file not found: {configPath}");
            return 2;
        }

        try
        {
            var text = File.ReadAllText(configPath);
            var doc = JsonNode.Parse(text);
            if (doc is null)
            {
                error.WriteLine("Configuration file is empty or invalid JSON.");
                return 2;
            }

            var workspaces = doc["workspaces"]?.AsArray();
            if (workspaces is null)
            {
                workspaces = new JsonArray();
                doc["workspaces"] = workspaces;
            }

            foreach (var existing in workspaces)
            {
                var existingId = (string?)existing?["workspaceId"];
                if (string.Equals(existingId, workspaceId, StringComparison.OrdinalIgnoreCase))
                {
                    error.WriteLine($"Workspace with ID '{workspaceId}' already exists pointing to '{existing?["rootPath"]}'.");
                    return 1;
                }
            }

            var modes = mode == "readOnly"
                ? new JsonArray { "readOnly" }
                : new JsonArray { "readOnly", "workspaceWrite" };

            var newWorkspace = new JsonObject
            {
                ["workspaceId"] = workspaceId,
                ["rootPath"] = fullPath,
                ["allowedHarnesses"] = new JsonArray { "shell", "claude", "codex" },
                ["allowedModes"] = modes
            };

            workspaces.Add(newWorkspace);

            var options = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(configPath, doc.ToJsonString(options));

            output.WriteLine($"Successfully added workspace '{workspaceId}' -> '{fullPath}'");
            output.WriteLine($"Configuration saved to {configPath}.");
            return 0;
        }
        catch (Exception ex)
        {
            error.WriteLine($"Failed to update {configPath}: {ex.Message}");
            return 2;
        }
    }

    private static int Remove(string[] args, string configPath, TextWriter output, TextWriter error)
    {
        if (args.Length < 3)
        {
            error.WriteLine("Usage: sentry-node workspace remove <workspaceId>");
            return 1;
        }

        var workspaceId = args[2].Trim();
        if (!File.Exists(configPath))
        {
            error.WriteLine($"Configuration file not found: {configPath}");
            return 2;
        }

        try
        {
            var text = File.ReadAllText(configPath);
            var doc = JsonNode.Parse(text);
            var workspaces = doc?["workspaces"]?.AsArray();
            if (workspaces is null)
            {
                error.WriteLine($"Workspace '{workspaceId}' not found.");
                return 1;
            }

            JsonNode? target = null;
            foreach (var existing in workspaces)
            {
                if (string.Equals((string?)existing?["workspaceId"], workspaceId, StringComparison.OrdinalIgnoreCase))
                {
                    target = existing;
                    break;
                }
            }

            if (target is null)
            {
                error.WriteLine($"Workspace '{workspaceId}' not found in {configPath}.");
                return 1;
            }

            workspaces.Remove(target);
            var options = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(configPath, doc!.ToJsonString(options));

            output.WriteLine($"Removed workspace '{workspaceId}' from {configPath}.");
            return 0;
        }
        catch (Exception ex)
        {
            error.WriteLine($"Failed to update {configPath}: {ex.Message}");
            return 2;
        }
    }

    public static string Slugify(string input)
    {
        var slug = Regex.Replace(input.ToLowerInvariant(), @"[^a-z0-9]+", "-").Trim('-');
        return string.IsNullOrWhiteSpace(slug) ? "workspace" : slug;
    }

    private static void PrintUsage(TextWriter output)
    {
        output.WriteLine("Sentry Node Workspace Management");
        output.WriteLine("Commands:");
        output.WriteLine("  workspace list");
        output.WriteLine("  workspace add <path> [--id <workspaceId>] [--mode readOnly|workspaceWrite]");
        output.WriteLine("  workspace remove <workspaceId>");
    }

    private static int Unknown(string verb, TextWriter output)
    {
        output.WriteLine($"Unknown workspace command: '{verb}'");
        PrintUsage(output);
        return 1;
    }
}
