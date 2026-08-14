// =============================================================================
// queries.ts — server state, and how often the console asks again
// =============================================================================
//
// Polling intervals are a product decision rather than a technical one, so they are
// collected here with their reasons instead of scattered as magic numbers.
//
// The console feels live because the fleet works while you watch, and the intervals
// below are chosen against what actually moves. They are also chosen against cost:
// CockroachDB Basic scales to zero and every poll is a query, so a one-second tick
// on an idle console would be a bill for watching nothing happen. Nothing here
// polls faster than two seconds, and only the surfaces you are looking at poll at
// all — react-query stops background refetching when the tab is hidden, which is
// the correct default and is left on deliberately.

import {
  useMutation, useQuery, useQueryClient, type UseQueryResult,
} from "@tanstack/react-query";
import { get, post, ApiError } from "./client";
import type { Campaign, CampaignSummary, GateState, IntentPlan, Proposal } from "./types";

/** Work lands on Today when an agent finishes; a few seconds late is fine. */
const TODAY_MS = 5_000;
/** A campaign's stages move as workers claim leases. This is the live one. */
const CAMPAIGN_MS = 2_500;
/** The gate changes when a provider is wired, which is a deploy, not a minute. */
const GATE_MS = 60_000;

export function useGate(): UseQueryResult<GateState, ApiError> {
  return useQuery({
    queryKey: ["gate"],
    queryFn: () => get<GateState>("/gate"),
    refetchInterval: GATE_MS,
    // The gate is chrome: a failure here must not blank the surface behind it.
    retry: 1,
  });
}

export function useToday(): UseQueryResult<{ proposals: Proposal[] }, ApiError> {
  return useQuery({
    queryKey: ["today"],
    queryFn: () => get<{ proposals: Proposal[] }>("/today"),
    refetchInterval: TODAY_MS,
  });
}

export function useCampaigns(): UseQueryResult<{ campaigns: CampaignSummary[] }, ApiError> {
  return useQuery({
    queryKey: ["campaigns"],
    queryFn: () => get<{ campaigns: CampaignSummary[] }>("/campaigns"),
    refetchInterval: TODAY_MS,
  });
}

export function useCampaign(id: string | undefined): UseQueryResult<Campaign, ApiError> {
  return useQuery({
    queryKey: ["campaign", id],
    queryFn: () => get<Campaign>(`/campaigns/${id}`),
    enabled: Boolean(id),
    refetchInterval: CAMPAIGN_MS,
  });
}

/**
 * Act on a proposal.
 *
 * Deliberately **not** optimistic. Optimism is right when the server is very likely
 * to agree and being wrong is cheap; here neither holds. Opening a thread takes a
 * counterparty off the market label-wide and can be refused by a unique index that
 * only the database can evaluate, and approving twice is refused by design. An
 * optimistic tick that then reverts would teach an operator to distrust the one
 * screen that must be trusted. The button waits, and says so.
 */
export function useAct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      post<{ ok: true; outcome?: string }>(`/proposals/${id}/${action}`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["today"] });
      void qc.invalidateQueries({ queryKey: ["gate"] });
    },
  });
}

export function useShortlistAct(campaignId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, action }: { contactId: string; action: string }) =>
      post<{ ok: true }>(`/campaigns/${campaignId}/shortlist/${contactId}/${action}`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
    },
  });
}

export function useRerunStage(campaignId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stageKey: string) =>
      post<{ ok: true }>(`/campaigns/${campaignId}/stages/${stageKey}/rerun`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
    },
  });
}

/** The intent surface asks the server what a stated goal would actually do. */
export function usePlanIntent() {
  return useMutation({
    mutationFn: (draft: {
      goal: string; artistId: string; channel: string; recordingId?: string | null;
    }) => post<IntentPlan>("/intent/plan", draft),
  });
}

export function useCommitIntent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (plan: IntentPlan) =>
      post<{ campaignId: string }>("/intent/commit", plan),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useArtists(): UseQueryResult<{ artists: { id: string; name: string }[] }, ApiError> {
  return useQuery({
    queryKey: ["artists"],
    queryFn: () => get<{ artists: { id: string; name: string }[] }>("/artists"),
    staleTime: 60_000,
  });
}
