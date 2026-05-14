import { lazy, Suspense, useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Button } from "@/components/ui/button";
import type {
	ApiKey,
	ApiKeyCreateRequest,
	ApiKeyUpdateRequest,
} from "@/features/api-keys/schemas";
import { ApiDetail } from "@/features/apis/components/api-detail";
import { ApiList } from "@/features/apis/components/api-list";
import { ApisSkeleton } from "@/features/apis/components/apis-skeleton";
import {
	useApiKeys,
	useApiKeyAccountUsage7Day,
	useApiKeyTrends,
	useApiKeyUsage7Day,
} from "@/features/apis/hooks/use-apis";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";

const ApiKeyCreateDialog = lazy(() =>
	import("@/features/api-keys/components/api-key-create-dialog").then((m) => ({
		default: m.ApiKeyCreateDialog,
	})),
);
const ApiKeyEditDialog = lazy(() =>
	import("@/features/api-keys/components/api-key-edit-dialog").then((m) => ({
		default: m.ApiKeyEditDialog,
	})),
);
const ApiKeyCreatedDialog = lazy(() =>
	import("@/features/api-keys/components/api-key-created-dialog").then((m) => ({
		default: m.ApiKeyCreatedDialog,
	})),
);

export function ApisPage() {
	const [searchParams, setSearchParams] = useSearchParams();
	const {
		apiKeysQuery,
		createMutation,
		updateMutation,
		deleteMutation,
		regenerateMutation,
	} = useApiKeys();

	const createDialog = useDialogState();
	const editDialog = useDialogState<ApiKey>();
	const deleteDialog = useDialogState<ApiKey>();
	const createdDialog = useDialogState<string>();

	const apiKeys = useMemo(() => apiKeysQuery.data ?? [], [apiKeysQuery.data]);
	const selectedKeyId = searchParams.get("selected");

	const handleSelectKey = useCallback(
		(keyId: string) => {
			const nextSearchParams = new URLSearchParams(searchParams);
			nextSearchParams.set("selected", keyId);
			setSearchParams(nextSearchParams);
		},
		[searchParams, setSearchParams],
	);

	const resolvedSelectedKeyId = useMemo(() => {
		if (apiKeys.length === 0) return null;
		if (selectedKeyId && apiKeys.some((k) => k.id === selectedKeyId))
			return selectedKeyId;
		return apiKeys[0].id;
	}, [apiKeys, selectedKeyId]);

	const selectedApiKey = useMemo(
		() =>
			resolvedSelectedKeyId
				? (apiKeys.find((k) => k.id === resolvedSelectedKeyId) ?? null)
				: null,
		[apiKeys, resolvedSelectedKeyId],
	);

	const trendsQuery = useApiKeyTrends(selectedApiKey?.id ?? null);
	const usage7DayQuery = useApiKeyUsage7Day(selectedApiKey?.id ?? null);
	const accountUsage7DayQuery = useApiKeyAccountUsage7Day(
		selectedApiKey?.id ?? null,
	);

	const mutationBusy =
		createMutation.isPending ||
		updateMutation.isPending ||
		deleteMutation.isPending ||
		regenerateMutation.isPending;

	const mutationError =
		getErrorMessageOrNull(createMutation.error) ||
		getErrorMessageOrNull(updateMutation.error) ||
		getErrorMessageOrNull(deleteMutation.error) ||
		getErrorMessageOrNull(regenerateMutation.error);
	const listError = getErrorMessageOrNull(apiKeysQuery.error);
	const usage7DayError = getErrorMessageOrNull(usage7DayQuery.error);
	const accountUsage7DayError = getErrorMessageOrNull(
		accountUsage7DayQuery.error,
	);
	const pageError = mutationError || (apiKeysQuery.data ? listError : null);

	const handleCreate = async (payload: ApiKeyCreateRequest) => {
		const created = await createMutation.mutateAsync(payload);
		createdDialog.show(created.key);
	};

	const handleUpdate = async (payload: ApiKeyUpdateRequest) => {
		if (!editDialog.data) return;
		await updateMutation.mutateAsync({ keyId: editDialog.data.id, payload });
	};

	return (
		<div className="animate-fade-in-up space-y-6">
			<div>
				<h1 className="text-2xl font-semibold tracking-tight">APIs</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Manage API keys for client access and usage monitoring.
				</p>
			</div>

			{pageError ? (
				<AlertMessage variant="error">{pageError}</AlertMessage>
			) : null}

			{apiKeysQuery.isPending && !apiKeysQuery.data ? (
				<ApisSkeleton />
			) : !apiKeysQuery.data ? (
				<div className="space-y-3 rounded-xl border bg-card p-4">
					<AlertMessage variant="error">
						{listError ?? "Failed to load API keys"}
					</AlertMessage>
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={() => {
							void apiKeysQuery.refetch();
						}}
						disabled={apiKeysQuery.isFetching}
					>
						Retry
					</Button>
				</div>
			) : (
				<div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
					<div className="rounded-xl border bg-card p-4">
						<ApiList
							apiKeys={apiKeys}
							selectedKeyId={resolvedSelectedKeyId}
							onSelect={handleSelectKey}
							onOpenCreate={() => createDialog.show()}
						/>
					</div>

					<ApiDetail
						apiKey={selectedApiKey}
						trends={trendsQuery.data}
						usage7Day={usage7DayQuery.data}
						usage7DayLoading={usage7DayQuery.isPending}
						usage7DayError={usage7DayError}
						accountUsage7Day={accountUsage7DayQuery.data}
						accountUsage7DayLoading={accountUsage7DayQuery.isPending}
						accountUsage7DayError={accountUsage7DayError}
						busy={mutationBusy}
						onEdit={(apiKey) => editDialog.show(apiKey)}
						onToggleActive={(apiKey) => {
							void updateMutation
								.mutateAsync({
									keyId: apiKey.id,
									payload: { isActive: !apiKey.isActive },
								})
								.catch(() => null);
						}}
						onDelete={(apiKey) => deleteDialog.show(apiKey)}
						onRegenerate={(apiKey) => {
							void regenerateMutation
								.mutateAsync(apiKey.id)
								.then((result) => {
									createdDialog.show(result.key);
								})
								.catch(() => null);
						}}
					/>
				</div>
			)}

			<Suspense fallback={null}>
				<ApiKeyCreateDialog
					open={createDialog.open}
					busy={createMutation.isPending}
					onOpenChange={createDialog.onOpenChange}
					onSubmit={handleCreate}
				/>

				<ApiKeyEditDialog
					open={editDialog.open}
					busy={updateMutation.isPending}
					apiKey={editDialog.data}
					onOpenChange={editDialog.onOpenChange}
					onSubmit={handleUpdate}
				/>

				<ApiKeyCreatedDialog
					open={createdDialog.open}
					apiKey={createdDialog.data}
					onOpenChange={createdDialog.onOpenChange}
				/>
			</Suspense>

			<ConfirmDialog
				open={deleteDialog.open}
				title="Delete API key"
				description="This key will stop working immediately."
				confirmLabel="Delete"
				onOpenChange={deleteDialog.onOpenChange}
				onConfirm={() => {
					if (!deleteDialog.data) return;
					void deleteMutation
						.mutateAsync(deleteDialog.data.id)
						.catch(() => null)
						.finally(() => {
							deleteDialog.hide();
						});
				}}
			/>

			<LoadingOverlay
				visible={!!apiKeysQuery.data && mutationBusy}
				label="Updating API keys..."
			/>
		</div>
	);
}
