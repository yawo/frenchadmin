import { useQuery } from "@tanstack/react-query";
import { getGraphContext, getGraphNeighbors } from "../api/client";

export function useGraphNeighbors(docId: string | null, hops = 1) {
  return useQuery({
    queryKey: ["graph-neighbors", docId, hops],
    queryFn: () => getGraphNeighbors(docId!, hops),
    enabled: !!docId,
  });
}

export function useGraphContext(docId: string | null) {
  return useQuery({
    queryKey: ["graph-context", docId],
    queryFn: () => getGraphContext(docId!),
    enabled: !!docId,
  });
}
