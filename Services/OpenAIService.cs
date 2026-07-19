using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using SentryAssistant.Models;

namespace SentryAssistant.Services;

public sealed class OpenAIService
{
    private readonly HttpClient _client = new();
    private readonly SettingsService _settings;

    public OpenAIService(SettingsService settings) => _settings = settings;

    private HttpRequestMessage CreateRequest(HttpMethod method, string path)
    {
        var request = new HttpRequestMessage(method, "https://api.openai.com/v1/" + path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _settings.GetApiKey());
        return request;
    }

    public async Task<string> AskAsync(string prompt, IReadOnlyList<AssistantMessage> history, CancellationToken cancellationToken = default)
    {
        var context = string.Join("\n", history.TakeLast(8).Select(message => $"{message.Speaker}: {message.Text}"));
        var body = new
        {
            model = _settings.Settings.AssistantModel,
            instructions = "You are Sentry, Bryce's concise personal assistant and code watcher. Be capable, direct, and conversational. Clearly distinguish completed work from suggestions. Never claim code or commands ran unless the supplied context proves it.",
            input = string.IsNullOrWhiteSpace(context) ? prompt : $"Recent conversation:\n{context}\n\nBryce: {prompt}",
            reasoning = new { effort = "minimal" },
            text = new { verbosity = "low" },
            max_output_tokens = 700
        };
        using var request = CreateRequest(HttpMethod.Post, "responses");
        request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        using var response = await _client.SendAsync(request, cancellationToken);
        var json = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException($"OpenAI returned HTTP {(int)response.StatusCode}.");
        return ExtractResponseText(json);
    }

    public async Task<string> SummarizeAsync(string text, CancellationToken cancellationToken = default)
    {
        if (!_settings.Settings.BriefResolutionSummaries) return text;
        var body = new
        {
            model = _settings.Settings.SummaryModel,
            instructions = $"Summarize the resolution as exactly one calm Jarvis-style sentence of no more than {_settings.Settings.SummaryWordLimit} words. State only the outcome; no greeting, list, question, or next step.",
            input = text,
            reasoning = new { effort = "minimal" },
            text = new { verbosity = "low" },
            max_output_tokens = 200
        };
        using var request = CreateRequest(HttpMethod.Post, "responses");
        request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        using var response = await _client.SendAsync(request, cancellationToken);
        var json = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode) return text;
        var summary = ExtractResponseText(json);
        return string.IsNullOrWhiteSpace(summary) ? text : summary;
    }

    public async Task<string> TranscribeAsync(string path, CancellationToken cancellationToken = default)
    {
        using var request = CreateRequest(HttpMethod.Post, "audio/transcriptions");
        using var form = new MultipartFormDataContent();
        await using var stream = File.OpenRead(path);
        using var file = new StreamContent(stream);
        file.Headers.ContentType = new MediaTypeHeaderValue("audio/wav");
        form.Add(file, "file", "sentry-reply.wav");
        form.Add(new StringContent(_settings.Settings.TranscriptionModel), "model");
        form.Add(new StringContent("en"), "language");
        request.Content = form;
        using var response = await _client.SendAsync(request, cancellationToken);
        var json = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException($"Transcription returned HTTP {(int)response.StatusCode}.");
        using var document = JsonDocument.Parse(json);
        return document.RootElement.GetProperty("text").GetString()?.Trim() ?? string.Empty;
    }

    public async Task<string> CreateSpeechAsync(string text, CancellationToken cancellationToken = default)
    {
        var body = new
        {
            model = _settings.Settings.SpeechModel,
            voice = _settings.Settings.SpeechVoice,
            input = text,
            instructions = _settings.Settings.VoiceStyle,
            response_format = "wav"
        };
        using var request = CreateRequest(HttpMethod.Post, "audio/speech");
        request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        using var response = await _client.SendAsync(request, cancellationToken);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException($"Speech returned HTTP {(int)response.StatusCode}.");
        var bytes = await response.Content.ReadAsByteArrayAsync(cancellationToken);
        RepairStreamingWavHeader(bytes);
        var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SentryAssistant");
        Directory.CreateDirectory(folder);
        var path = Path.Combine(folder, "latest-response.wav");
        await File.WriteAllBytesAsync(path, bytes, cancellationToken);
        return path;
    }

    private static string ExtractResponseText(string json)
    {
        using var document = JsonDocument.Parse(json);
        foreach (var output in document.RootElement.GetProperty("output").EnumerateArray())
        {
            if (output.GetProperty("type").GetString() != "message") continue;
            foreach (var content in output.GetProperty("content").EnumerateArray())
            {
                if (content.GetProperty("type").GetString() == "output_text")
                    return content.GetProperty("text").GetString()?.Trim() ?? string.Empty;
            }
        }
        return string.Empty;
    }

    private static void RepairStreamingWavHeader(byte[] bytes)
    {
        if (bytes.Length < 44) return;
        if (BitConverter.ToUInt32(bytes, 4) == uint.MaxValue) BitConverter.GetBytes((uint)(bytes.Length - 8)).CopyTo(bytes, 4);
        if (Encoding.ASCII.GetString(bytes, 36, 4) == "data" && BitConverter.ToUInt32(bytes, 40) == uint.MaxValue)
            BitConverter.GetBytes((uint)(bytes.Length - 44)).CopyTo(bytes, 40);
    }
}
