using System.Text.Json;

namespace Sentry.Contracts.Tests;

public sealed class WorkOrderContractTests
{
    private static readonly DateTimeOffset Now = new(2026, 7, 19, 12, 0, 0, TimeSpan.Zero);

    [Fact]
    public void Work_order_round_trips_with_string_enums()
    {
        var order = ValidOrder();

        var json = JsonSerializer.Serialize(order, SentryContractJson.Options);
        var copy = JsonSerializer.Deserialize<WorkOrder>(json, SentryContractJson.Options);

        Assert.NotNull(copy);
        Assert.Equal(order.Id, copy.Id);
        Assert.Equal(order.State, copy.State);
        Assert.Equal(order.Mode, copy.Mode);
        Assert.Equal(order.CompletionCriteria, copy.CompletionCriteria);
        Assert.Contains("\"state\":\"assigned\"", json);
        Assert.Contains("\"mode\":\"workspaceWrite\"", json);
    }

    [Theory]
    [InlineData("state", "invented")]
    [InlineData("mode", "reckless")]
    public void Unknown_enum_names_are_rejected(string property, string value)
    {
        var json = JsonSerializer.Serialize(ValidOrder(), SentryContractJson.Options);
        json = property == "state"
            ? json.Replace("\"state\":\"assigned\"", $"\"state\":\"{value}\"")
            : json.Replace("\"mode\":\"workspaceWrite\"", $"\"mode\":\"{value}\"");

        Assert.Throws<JsonException>(() =>
            JsonSerializer.Deserialize<WorkOrder>(json, SentryContractJson.Options));
    }

    [Fact]
    public void Numeric_enum_values_are_rejected()
    {
        var json = JsonSerializer.Serialize(ValidOrder(), SentryContractJson.Options)
            .Replace("\"state\":\"assigned\"", "\"state\":999");

        Assert.Throws<JsonException>(() =>
            JsonSerializer.Deserialize<WorkOrder>(json, SentryContractJson.Options));
    }

    [Fact]
    public void Undefined_constructed_enum_is_invalid()
    {
        var result = WorkOrderValidator.Validate(
            ValidOrder() with { State = (WorkOrderState)999 },
            Now);

        Assert.False(result.IsValid);
        Assert.Contains(result.Errors, error => error.Code == "work_order.state_unknown");
    }

    [Fact]
    public void Expired_work_order_is_invalid()
    {
        var result = WorkOrderValidator.Validate(ValidOrder() with { ExpiresAt = Now }, Now);

        Assert.False(result.IsValid);
        Assert.Contains(result.Errors, error => error.Code == "work_order.expired");
    }

    [Theory]
    [InlineData("id")]
    [InlineData("requester")]
    [InlineData("profile")]
    [InlineData("conversation")]
    public void Required_identity_is_enforced(string field)
    {
        var order = ValidOrder();
        order = field switch
        {
            "id" => order with { Id = Guid.Empty },
            "requester" => order with { RequestedByUserId = Guid.Empty },
            "profile" => order with { ProfileId = Guid.Empty },
            "conversation" => order with { ConversationSessionId = Guid.Empty },
            _ => order
        };

        Assert.False(WorkOrderValidator.Validate(order, Now).IsValid);
    }

    [Fact]
    public void Empty_completion_criteria_are_invalid()
    {
        var result = WorkOrderValidator.Validate(
            ValidOrder() with { CompletionCriteria = Array.Empty<string>() },
            Now);

        Assert.False(result.IsValid);
        Assert.Contains(result.Errors, error => error.Code == "work_order.criteria_required");
    }

    [Fact]
    public void Blank_completion_criteria_are_invalid()
    {
        var result = WorkOrderValidator.Validate(
            ValidOrder() with { CompletionCriteria = ["   "] },
            Now);

        Assert.False(result.IsValid);
        Assert.Contains(result.Errors, error => error.Code == "work_order.criteria_blank");
    }

    [Fact]
    public void Draft_can_omit_assignment_details()
    {
        var draft = ValidOrder() with
        {
            State = WorkOrderState.Draft,
            AssignedUserId = null,
            ExecutionNodeId = null,
            Harness = null,
            WorkspaceId = null
        };

        Assert.True(WorkOrderValidator.Validate(draft, Now).IsValid);
    }

    [Theory]
    [InlineData(WorkOrderState.Assigned)]
    [InlineData(WorkOrderState.InProgress)]
    public void Active_work_requires_complete_assignment(WorkOrderState state)
    {
        var order = ValidOrder() with
        {
            State = state,
            AssignedUserId = null,
            ExecutionNodeId = null,
            Harness = null,
            WorkspaceId = null
        };

        var result = WorkOrderValidator.Validate(order, Now);

        Assert.False(result.IsValid);
        Assert.Equal(4, result.Errors.Count(error => error.Code.StartsWith("work_order.assignment_")));
    }

    [Fact]
    public void Harness_and_notification_events_round_trip()
    {
        using var evidence = JsonDocument.Parse("{\"commit\":\"abc123\"}");
        var harnessEvent = new HarnessEvent(
            Guid.NewGuid(), "grok-build", "task.resolved", Now,
            "Foundation ready.", evidence.RootElement.Clone());
        var notification = new NotificationEnvelope(
            Guid.NewGuid(), "task.resolved", NotificationSeverity.Important,
            "Resolved", "Foundation ready.", new Uri("sentry://work-orders/1"),
            ["desktop", "apns"], Now.AddMinutes(5), "corr-1");

        var harnessCopy = RoundTrip(harnessEvent);
        var notificationCopy = RoundTrip(notification);

        Assert.Equal("grok-build", harnessCopy.Harness);
        Assert.Equal("abc123", harnessCopy.Evidence.GetProperty("commit").GetString());
        Assert.Equal(NotificationSeverity.Important, notificationCopy.Severity);
    }

    private static T RoundTrip<T>(T value)
    {
        var json = JsonSerializer.Serialize(value, SentryContractJson.Options);
        return JsonSerializer.Deserialize<T>(json, SentryContractJson.Options)!;
    }

    private static WorkOrder ValidOrder() => new(
        Guid.NewGuid(),
        Guid.NewGuid(),
        Guid.NewGuid(),
        Guid.NewGuid(),
        Guid.NewGuid(),
        Guid.NewGuid(),
        Guid.NewGuid(),
        "codex",
        "sentry-foundation",
        "Build the Sentry foundation.",
        WorkOrderState.Assigned,
        WorkOrderMode.WorkspaceWrite,
        Now.AddHours(1),
        ["Contracts pass", "Shell launches"],
        "corr-1");
}
