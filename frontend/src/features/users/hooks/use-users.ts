import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createUser,
  deleteUser,
  listUsers,
  updateUser,
  type ListUsersParams,
} from "@/features/users/api";
import type { DashboardUserCreateRequest, DashboardUserUpdateRequest } from "@/features/users/schemas";

export type UseUsersFilters = {
  search?: string;
  role?: ListUsersParams["role"];
};

export function useUsers(filters: UseUsersFilters = {}, enabled = true) {
  const queryClient = useQueryClient();

  const usersQuery = useQuery({
    queryKey: ["users", "list", filters],
    queryFn: () => listUsers(filters),
    select: (data) => data.users,
    enabled,
  });

  const invalidateUsers = () => {
    void queryClient.invalidateQueries({ queryKey: ["users", "list"] });
  };

  const createMutation = useMutation({
    mutationFn: (payload: DashboardUserCreateRequest) => createUser(payload),
    onSuccess: () => {
      toast.success("User created");
      invalidateUsers();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to create user");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ userId, payload }: { userId: string; payload: DashboardUserUpdateRequest }) =>
      updateUser(userId, payload),
    onSuccess: () => {
      toast.success("User updated");
      invalidateUsers();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update user");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (userId: string) => deleteUser(userId),
    onSuccess: () => {
      toast.success("User deleted");
      invalidateUsers();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete user");
    },
  });

  return {
    usersQuery,
    createMutation,
    updateMutation,
    deleteMutation,
  };
}
