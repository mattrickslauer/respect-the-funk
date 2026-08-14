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
import type {
  Budget, Campaign, CampaignSummary, FleetAgent, IntentPlan, Proposal, Summary,
} from "./types";

/** Work lands on Today when an agent finishes; a few seconds late is fine. */
const TODAY_MS = 5_000;
/** A campaign's stages move as workers claim leases. This is the live one. */
const CAMPAIGN_MS = 2_500;
/** Eleven numbers in one round trip — what the API recommends polling. */
const SUMMARY_MS = 10_000;

/**
 * The counts, and whether a sender exists at all.
 *
 * This is the endpoint the API explicitly recommends polling in place of holding a
 * changefeed open — one statement for eleven numbers, and cheaper at a few seconds'
 * interval than a feed per browser tab. The console's send-gate indicator is built
 * from `sender_wired` and `queued_unsent` here rather than from a bespoke endpoint.
 */
export function useSummary(): UseQueryResult<Summary, ApiError> {
  return useQuery({
    queryKey: ["summary"],
    queryFn: () => get<Summary>("/summary"),
    refetchInterval: SUMMARY_MS,
    // Chrome: a failure here must not blank the surface behind it.
    retry: 1,
  });
}

export function useFleet(): UseQueryResult<{ agents: FleetAgent[] }, ApiError> {
  return useQuery({
    queryKey: ["fleet"],
    queryFn: () => get<{ agents: FleetAgent[] }>("/fleet"),
    staleTime: 30_000,
  });
}

export function useBudgets(): UseQueryResult<{ budgets: Budget[] }, ApiError> {
  return useQuery({
    queryKey: ["budgets"],
    queryFn: () => get<{ budgets: Budget[] }>("/budgets"),
    staleTime: 30_000,
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
      void qc.invalidateQueries({ queryKey: ["summary"] });
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

/**
 * Create the campaign a plan describes.
 *
 * The plan itself is composed on the client from `/fleet` and `/budgets` — see
 * `IntentPlan` — so the only thing that crosses the wire here is the campaign, and
 * it is created as a draft that opens nothing. Running it stays a second,
 * deliberate act.
 */
export function useCommitIntent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (plan: IntentPlan) =>
      post<{ id: string }>("/campaigns", {
        artist_id: plan.artistId,
        channel: plan.channel,
        goal: plan.goal,
        recording_id: plan.recordingId ?? null,
        cap_micro_usd: plan.capMicroUsd,
      }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useArtists(): UseQueryResult<
  { artists: { id: string; name: string; status: string }[] }, ApiError
> {
  return useQuery({
    queryKey: ["artists"],
    queryFn: () =>
      get<{ artists: { id: string; name: string; status: string }[] }>("/artists"),
    staleTime: 60_000,
  });
}
