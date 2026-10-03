import { List, PanelsTopLeft, ShieldCheck } from "lucide-react";
import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { LoadingOverlay } from "@/components/layout/loading-overlay";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { ApiViewMode } from "@/features/apis/list-utils";
import type {
	ApiKey,
	ApiKeyCreateRequest,
	ApiKeyUpdateRequest,
} from "@/features/api-keys/schemas";
import { ApiKeyCreatedDialog } from "@/features/api-keys/components/api-key-created-dialog";
import { ApiKeysOverview } from "@/features/api-keys/components/api-keys-overview";
import { ApiDetail } from "@/features/apis/components/api-detail";
import { ApiList } from "@/features/apis/components/api-list";
import { ApisSkeleton } from "@/features/apis/components/apis-skeleton";
import {
	useApiKeys,
	useApiKeyTrends,
	useApiKeyUsage7Day,
} from "@/features/apis/hooks/use-apis";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
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

export function ApisPage() {
	const { t } = useTranslation();
	const [searchParams, setSearchParams] = useSearchParams();
	const [viewMode, setViewMode] = useState<ApiViewMode>(() => {
		try {
			return localStorage.getItem("codex-lb-apis-view-mode") === "list"
				? "list"
				: "detail";
		} catch {
			return "detail";
		}
	});
	const [detailOpen, setDetailOpen] = useState(
		Boolean(searchParams.get("selected")),
	);
	const changeView = (mode: ApiViewMode) => {
		setViewMode(mode);
		setDetailOpen(false);
		try {
			localStorage.setItem("codex-lb-apis-view-mode", mode);
		} catch {
			/* View switching remains usable when storage is unavailable. */
		}
	};
	const canReadKeys = usePermission("api_keys:read");
	const canWrite = useAuthStore((state) => state.canWrite);
	const {
		apiKeysQuery,
		createMutation,
		updateMutation,
		deleteMutation,
		regenerateMutation,
	} = useApiKeys({ enabled: canReadKeys });

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
			setDetailOpen(viewMode === "list");
		},
		[searchParams, setSearchParams, viewMode],
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

	const visibleKeyId =
		viewMode === "detail" || detailOpen ? (selectedApiKey?.id ?? null) : null;
	const trendsQuery = useApiKeyTrends(visibleKeyId, { enabled: canReadKeys });
	const usage7DayQuery = useApiKeyUsage7Day(visibleKeyId, { enabled: canReadKeys });

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
	const pageError = mutationError || (apiKeysQuery.data ? listError : null);

	const handleCreate = async (payload: ApiKeyCreateRequest) => {
		const created = await createMutation.mutateAsync(payload);
		createdDialog.show(created.key);
	};

	const handleUpdate = async (payload: ApiKeyUpdateRequest) => {
		if (!editDialog.data) return;
		await updateMutation.mutateAsync({ keyId: editDialog.data.id, payload });
	};

	if (!canReadKeys) {
		return (
			<div className="animate-fade-in-up space-y-6">
				<div>
					<h1 className="text-2xl font-semibold tracking-tight">{t("apis.page.title")}</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						{t("apis.page.subtitle")}
					</p>
				</div>
				<div
					role="status"
					className="flex flex-col items-center gap-2 rounded-xl border border-dashed bg-card p-8 text-center"
				>
					<div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
						<ShieldCheck className="h-5 w-5 text-primary" aria-hidden="true" />
					</div>
					<p className="text-sm font-medium">{t("apis.page.adminOnlyTitle")}</p>
					<p className="text-xs text-muted-foreground">{t("apis.page.adminOnlyDescription")}</p>
				</div>
			</div>
		);
	}


	const detailPanel = (
		<ApiDetail
			apiKey={selectedApiKey}
			trends={trendsQuery.data}
			usage7Day={usage7DayQuery.data}
			usage7DayLoading={usage7DayQuery.isPending}
			usage7DayError={usage7DayError}
			busy={mutationBusy}
			readOnly={!canWrite}
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
	);

	return (
		<div className="animate-fade-in-up space-y-6">
			<div className="flex flex-wrap items-start justify-between gap-4">
				<div>
					<h1 className="text-2xl font-semibold tracking-tight">
						{t("apis.page.title")}
					</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						{t("apis.page.subtitle")}
					</p>
				</div>
				<div
					className="flex flex-wrap gap-1 rounded-lg border bg-card p-1"
					role="group"
					aria-label={t("apis.view.label")}
				>
					{(["detail", "list"] as const).map((mode) => {
						const Icon = mode === "detail" ? PanelsTopLeft : List;
						return (
							<Button
								key={mode}
								type="button"
								variant={viewMode === mode ? "secondary" : "ghost"}
								size="sm"
								aria-pressed={viewMode === mode}
								onClick={() => changeView(mode)}
							>
								<Icon className="mr-1 size-4" aria-hidden />
								{t(`apis.view.${mode}`)}
							</Button>
						);
					})}
				</div>
			</div>

			{pageError ? (
				<AlertMessage variant="error">{pageError}</AlertMessage>
			) : null}

			{apiKeysQuery.isPending && !apiKeysQuery.data ? (
				<ApisSkeleton />
			) : !apiKeysQuery.data ? (
				<div className="space-y-3 rounded-xl border bg-card p-4">
					<AlertMessage variant="error">
						{listError ?? t("apiKeys.toasts.loadFailed")}
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
						{t("common.actions.retry")}
					</Button>
				</div>
			) : (
				<div className="space-y-6">
					<ApiKeysOverview apiKeys={apiKeys} />

					<div
						className={cn(
							"grid min-w-0 gap-4",
							viewMode === "detail" && "lg:grid-cols-[22rem_minmax(0,1fr)]",
						)}
					>
						<div className="min-w-0 rounded-xl border bg-card p-3 sm:p-4">
							<ApiList
								apiKeys={apiKeys}
								viewMode={viewMode}
								selectedKeyId={resolvedSelectedKeyId}
								onSelect={handleSelectKey}
								onOpenCreate={() => createDialog.show()}
								readOnly={!canWrite}
							/>
						</div>

						{viewMode === "detail" ? detailPanel : null}
					</div>
				</div>
			)}

			<Dialog
				open={viewMode === "list" && detailOpen && !!selectedApiKey}
				onOpenChange={setDetailOpen}
			>
				<DialogContent
					className="max-h-[90dvh] overflow-y-auto sm:max-w-5xl"
					aria-describedby={undefined}
				>
					<DialogTitle className="sr-only">
						{t("apis.list.detailsFor", { name: selectedApiKey?.name ?? "" })}
					</DialogTitle>
					{detailPanel}
				</DialogContent>
			</Dialog>
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
			</Suspense>

			<ApiKeyCreatedDialog
				open={createdDialog.open}
				apiKey={createdDialog.data}
				onOpenChange={createdDialog.onOpenChange}
			/>

			<ConfirmDialog
				open={deleteDialog.open}
				title={t("apiKeys.deleteDialog.title")}
				description={t("apiKeys.deleteDialog.description")}
				confirmLabel={t("common.actions.delete")}
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
				label={t("apiKeys.page.updating")}
			/>
		</div>
	);
}
