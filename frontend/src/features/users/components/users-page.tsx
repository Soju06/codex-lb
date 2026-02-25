import { Plus, UserCog } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useUsers } from "@/features/users/hooks/use-users";
import type {
  DashboardUser,
  DashboardUserRole,
  DashboardUserUpdateRequest,
} from "@/features/users/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong, formatSlug } from "@/utils/formatters";

type UserRoleFilter = "all" | DashboardUserRole;

type CreateFormState = {
  username: string;
  password: string;
  role: DashboardUserRole;
};

type EditFormState = {
  username: string;
  password: string;
  role: DashboardUserRole;
  isActive: boolean;
};

const DEFAULT_CREATE_FORM: CreateFormState = {
  username: "",
  password: "",
  role: "user",
};

function roleBadgeClass(role: DashboardUserRole): string {
  return role === "admin" ? "bg-blue-500 text-white" : "bg-emerald-500 text-white";
}

function statusBadgeClass(isActive: boolean): string {
  return isActive ? "bg-emerald-500 text-white" : "bg-zinc-500 text-white";
}

export function UsersPage() {
  const currentUserId = useAuthStore((state) => state.user?.id);

  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<UserRoleFilter>("all");

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(DEFAULT_CREATE_FORM);

  const [editUser, setEditUser] = useState<DashboardUser | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<EditFormState | null>(null);

  const [deleteUser, setDeleteUser] = useState<DashboardUser | null>(null);

  const filters = useMemo(
    () => ({
      search: search.trim() || undefined,
      role: roleFilter === "all" ? undefined : roleFilter,
    }),
    [roleFilter, search],
  );

  const { usersQuery, createMutation, updateMutation, deleteMutation } = useUsers(filters);

  const users = usersQuery.data ?? [];
  const busy =
    usersQuery.isFetching || createMutation.isPending || updateMutation.isPending || deleteMutation.isPending;

  const errorMessage =
    getErrorMessageOrNull(usersQuery.error) ||
    getErrorMessageOrNull(createMutation.error) ||
    getErrorMessageOrNull(updateMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error);

  const openCreateDialog = () => {
    setCreateForm(DEFAULT_CREATE_FORM);
    setCreateOpen(true);
  };

  const openEditDialog = (user: DashboardUser) => {
    setEditUser(user);
    setEditForm({
      username: user.username,
      password: "",
      role: user.role,
      isActive: user.isActive,
    });
    setEditOpen(true);
  };

  const handleCreateSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await createMutation.mutateAsync({
      username: createForm.username,
      password: createForm.password,
      role: createForm.role,
    });
    setCreateOpen(false);
    setCreateForm(DEFAULT_CREATE_FORM);
  };

  const handleEditSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editUser || !editForm) {
      return;
    }

    const payload: DashboardUserUpdateRequest = {};
    if (editForm.username.trim() !== editUser.username) {
      payload.username = editForm.username.trim();
    }
    if (editForm.password.trim()) {
      payload.password = editForm.password.trim();
    }
    if (editForm.role !== editUser.role) {
      payload.role = editForm.role;
    }
    if (editForm.isActive !== editUser.isActive) {
      payload.isActive = editForm.isActive;
    }

    if (Object.keys(payload).length === 0) {
      setEditOpen(false);
      return;
    }

    await updateMutation.mutateAsync({
      userId: editUser.id,
      payload,
    });
    setEditOpen(false);
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage dashboard users, roles, and account access.
          </p>
        </div>
        <Button type="button" size="sm" className="h-8 gap-1.5 text-xs" onClick={openCreateDialog} disabled={busy}>
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          Add user
        </Button>
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      <section className="space-y-3 rounded-xl border bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by username..."
            className="h-8 w-full sm:w-80"
          />
          <Select value={roleFilter} onValueChange={(value) => setRoleFilter(value as UserRoleFilter)}>
            <SelectTrigger size="sm" className="w-full sm:w-44">
              <SelectValue placeholder="Role" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All roles</SelectItem>
              <SelectItem value="admin">Admin</SelectItem>
              <SelectItem value="user">User</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {users.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center">
            <UserCog className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm font-medium text-muted-foreground">No users found</p>
            <p className="text-xs text-muted-foreground/70">Try adjusting filters or create a new account.</p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Username</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => {
                  const created = formatTimeLong(user.createdAt);
                  const updated = formatTimeLong(user.updatedAt);
                  const isCurrentUser = user.id === currentUserId;
                  return (
                    <TableRow key={user.id}>
                      <TableCell className="font-medium">{user.username}</TableCell>
                      <TableCell>
                        <Badge className={roleBadgeClass(user.role)}>{formatSlug(user.role)}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge className={statusBadgeClass(user.isActive)}>
                          {user.isActive ? "Active" : "Inactive"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{created.date}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{updated.date}</TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex items-center gap-2">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs"
                            onClick={() => openEditDialog(user)}
                            disabled={busy}
                          >
                            Edit
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs text-destructive hover:text-destructive"
                            onClick={() => setDeleteUser(user)}
                            disabled={busy || isCurrentUser}
                            title={isCurrentUser ? "Current user cannot be deleted" : undefined}
                          >
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create user</DialogTitle>
            <DialogDescription>Add a new dashboard account and role.</DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={(event) => void handleCreateSubmit(event)}>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="create-username">
                Username
              </label>
              <Input
                id="create-username"
                value={createForm.username}
                onChange={(event) =>
                  setCreateForm((prev) => ({
                    ...prev,
                    username: event.target.value,
                  }))
                }
                autoComplete="username"
                required
                disabled={busy}
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="create-password">
                Password
              </label>
              <Input
                id="create-password"
                type="password"
                value={createForm.password}
                onChange={(event) =>
                  setCreateForm((prev) => ({
                    ...prev,
                    password: event.target.value,
                  }))
                }
                autoComplete="new-password"
                minLength={8}
                required
                disabled={busy}
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="create-role">
                Role
              </label>
              <Select
                value={createForm.role}
                onValueChange={(value) =>
                  setCreateForm((prev) => ({
                    ...prev,
                    role: value as DashboardUserRole,
                  }))
                }
              >
                <SelectTrigger id="create-role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Admin</SelectItem>
                  <SelectItem value="user">User</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={busy}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={editOpen}
        onOpenChange={(open) => {
          setEditOpen(open);
          if (!open) {
            setEditUser(null);
            setEditForm(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit user</DialogTitle>
            <DialogDescription>Update role, status, or reset password.</DialogDescription>
          </DialogHeader>
          {editUser && editForm ? (
            <form className="space-y-4" onSubmit={(event) => void handleEditSubmit(event)}>
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="edit-username">
                  Username
                </label>
                <Input
                  id="edit-username"
                  value={editForm.username}
                  onChange={(event) =>
                    setEditForm((prev) => (prev ? { ...prev, username: event.target.value } : prev))
                  }
                  autoComplete="username"
                  required
                  disabled={busy}
                />
              </div>

              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="edit-password">
                  New password
                </label>
                <Input
                  id="edit-password"
                  type="password"
                  value={editForm.password}
                  onChange={(event) =>
                    setEditForm((prev) => (prev ? { ...prev, password: event.target.value } : prev))
                  }
                  autoComplete="new-password"
                  minLength={8}
                  placeholder="Leave blank to keep current"
                  disabled={busy}
                />
              </div>

              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="edit-role">
                  Role
                </label>
                <Select
                  value={editForm.role}
                  onValueChange={(value) =>
                    setEditForm((prev) => (prev ? { ...prev, role: value as DashboardUserRole } : prev))
                  }
                >
                  <SelectTrigger id="edit-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Admin</SelectItem>
                    <SelectItem value="user">User</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex items-center justify-between rounded-lg border p-3">
                <div>
                  <p className="text-sm font-medium">Active user</p>
                  <p className="text-xs text-muted-foreground">Inactive users cannot sign in.</p>
                </div>
                <Switch
                  checked={editForm.isActive}
                  disabled={busy}
                  onCheckedChange={(checked) =>
                    setEditForm((prev) => (prev ? { ...prev, isActive: checked } : prev))
                  }
                />
              </div>

              <DialogFooter>
                <Button type="submit" disabled={busy}>
                  Save
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteUser !== null}
        title="Delete user"
        description={
          deleteUser
            ? `Delete user '${deleteUser.username}'. This action cannot be undone.`
            : "Delete user"
        }
        confirmLabel="Delete"
        cancelLabel="Cancel"
        onOpenChange={(open) => {
          if (!open) {
            setDeleteUser(null);
          }
        }}
        onConfirm={() => {
          if (!deleteUser) {
            return;
          }
          void deleteMutation.mutateAsync(deleteUser.id).finally(() => {
            setDeleteUser(null);
          });
        }}
      />
    </div>
  );
}
