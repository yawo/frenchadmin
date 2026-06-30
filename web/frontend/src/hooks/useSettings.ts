import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getLLMSettings, updateLLMSettings } from "../api/client";
import type { LLMSettings } from "../types";

export function useSettings() {
  const queryClient = useQueryClient();

  const query = useQuery<LLMSettings>({
    queryKey: ["llm-settings"],
    queryFn: getLLMSettings,
  });

  const mutation = useMutation({
    mutationFn: updateLLMSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(["llm-settings"], data);
    },
  });

  return {
    settings: query.data,
    isLoading: query.isLoading,
    update: mutation.mutateAsync,
    isUpdating: mutation.isPending,
  };
}
