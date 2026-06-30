import { useMutation } from "@tanstack/react-query";
import { search } from "../api/client";
import type { SearchRequest } from "../types";

export function useSearch() {
  return useMutation({
    mutationFn: (params: SearchRequest) => search(params),
  });
}
