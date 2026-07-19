namespace Sentry.Contracts;

public sealed record WorkOrder(
    Guid Id,
    Guid RequestedByUserId,
    Guid ProfileId,
    Guid? TeamId,
    Guid ConversationSessionId,
    Guid? AssignedUserId,
    Guid? ExecutionNodeId,
    string? Harness,
    string? WorkspaceId,
    string Prompt,
    WorkOrderState State,
    WorkOrderMode Mode,
    DateTimeOffset ExpiresAt,
    IReadOnlyList<string> CompletionCriteria,
    string CorrelationId);

public enum WorkOrderMode
{
    ReadOnly,
    WorkspaceWrite,
    ApprovedElevated
}

public enum WorkOrderState
{
    Draft,
    Submitted,
    NeedsClarification,
    Triaged,
    Assigned,
    InProgress,
    NeedsInput,
    ReadyForReview,
    ChangesRequested,
    Resolved,
    Closed,
    Cancelled,
    Failed
}

public sealed record ContractValidationError(string Code, string Message);

public sealed record ContractValidationResult(IReadOnlyList<ContractValidationError> Errors)
{
    public bool IsValid => Errors.Count == 0;
}

public static class WorkOrderValidator
{
    public static ContractValidationResult Validate(WorkOrder order, DateTimeOffset now)
    {
        ArgumentNullException.ThrowIfNull(order);

        var errors = new List<ContractValidationError>();

        RequireIdentity(order.Id, "id", errors);
        RequireIdentity(order.RequestedByUserId, "requester", errors);
        RequireIdentity(order.ProfileId, "profile", errors);
        RequireIdentity(order.ConversationSessionId, "conversation", errors);

        if (order.ExpiresAt <= now)
        {
            errors.Add(new("work_order.expired", "The work order has expired."));
        }

        if (!Enum.IsDefined(order.State))
        {
            errors.Add(new("work_order.state_unknown", "The work-order state is not recognized."));
        }

        if (!Enum.IsDefined(order.Mode))
        {
            errors.Add(new("work_order.mode_unknown", "The work-order mode is not recognized."));
        }

        if (string.IsNullOrWhiteSpace(order.Prompt))
        {
            errors.Add(new("work_order.prompt_required", "A work-order prompt is required."));
        }

        if (string.IsNullOrWhiteSpace(order.CorrelationId))
        {
            errors.Add(new("work_order.correlation_required", "A correlation ID is required."));
        }

        if (order.CompletionCriteria is null || order.CompletionCriteria.Count == 0)
        {
            errors.Add(new("work_order.criteria_required", "At least one completion criterion is required."));
        }
        else if (order.CompletionCriteria.Any(string.IsNullOrWhiteSpace))
        {
            errors.Add(new("work_order.criteria_blank", "Completion criteria cannot be blank."));
        }

        if (order.State is WorkOrderState.Assigned or WorkOrderState.InProgress)
        {
            RequireAssignment(order.AssignedUserId is not null && order.AssignedUserId != Guid.Empty,
                "assignee", errors);
            RequireAssignment(order.ExecutionNodeId is not null && order.ExecutionNodeId != Guid.Empty,
                "node", errors);
            RequireAssignment(!string.IsNullOrWhiteSpace(order.Harness), "harness", errors);
            RequireAssignment(!string.IsNullOrWhiteSpace(order.WorkspaceId), "workspace", errors);
        }

        return new(errors);
    }

    private static void RequireIdentity(
        Guid id,
        string field,
        ICollection<ContractValidationError> errors)
    {
        if (id == Guid.Empty)
        {
            errors.Add(new($"work_order.{field}_required", $"A {field} identity is required."));
        }
    }

    private static void RequireAssignment(
        bool condition,
        string field,
        ICollection<ContractValidationError> errors)
    {
        if (!condition)
        {
            errors.Add(new($"work_order.assignment_{field}_required",
                $"An assigned work order requires a {field}."));
        }
    }
}
