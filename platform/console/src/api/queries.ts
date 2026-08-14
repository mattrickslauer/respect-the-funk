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
import { get, getListing, post, ApiError, type Listing } from "./client";
import type {
  Budget, Campaign, CampaignSummary, FleetAgent, IntentPlan, Summary, TodayAction,
  TodayResponse, TodayRow,
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

export function useFleet(): UseQueryResult<Listing<FleetAgent>, ApiError> {
  return useQuery({
    queryKey: ["fleet"],
    queryFn: () => getListing<FleetAgent>("/fleet"),
    staleTime: 30_000,
  });
}

export function useBudgets(): UseQueryResult<Listing<Budget>, ApiError> {
  return useQuery({
    queryKey: ["budgets"],
    queryFn: () => getListing<Budget>("/budgets"),
    staleTime: 30_000,
  });
}

export function useToday(): UseQueryResult<TodayResponse, ApiError> {
  return useQuery({
    queryKey: ["today"],
    queryFn: () => get<TodayResponse>("/today"),
    refetchInterval: TODAY_MS,
  });
}

export function useCampaigns(): UseQueryResult<Listing<CampaignSummary>, ApiError> {
  return useQuery({
    queryKey: ["campaigns"],
    queryFn: () => getListing<CampaignSummary>("/campaigns"),
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
    mutationFn: async ({ row, action }: { row: TodayRow; action: TodayAction }) => {
      // The server sends the address; the client substitutes the id and posts. A
      // route can move without a client release, and there is no second place that
      // knows what the URL for "accept a suggestion" is.
      //
      // `per: "candidate"` fans the press out across the group. The operator manual
      // is explicit that a suggestion group is one decision per artist — "accept or
      // reject the lot, in one go" — so a single press must mean the lot, not the
      // first of five.
      const ids = action.per === "candidate"
        ? (row.candidates ?? []).map((c) => c.id)
        : [row.id];

      if (ids.length === 0) {
        throw new Error(
          "This row has no candidates to act on. Nothing was sent.",
        );
      }

      // Sequential, not `Promise.all`. These are writes against a cluster that
      // enforces its own constraints, and a partial failure has to be reportable as
      // "three of five went through" rather than as one rejected promise hiding the
      // rest. The counts are small — a suggestion group is single digits.
      const done: string[] = [];
      for (const id of ids) {
        const path = action.endpoint
          .replace(/^\/api\/v1/, "")
          .replace("{id}", encodeURIComponent(id));
        try {
          await post<unknown>(path);
          done.push(id);
        } catch (err) {
          if (done.length > 0) {
            throw new Error(
              `${done.length} of ${ids.length} went through, then: ` +
              `${err instanceof Error ? err.message : String(err)}`,
            );
          }
          throw err;
        }
      }
      return { applied: done.length };
    },
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
 * No cap crosses the wire, and that is the API's correction rather than an
 * omission: there is no campaign-level cap column, because caps are per artist in
 * `party_budget`. The API refused to accept a `cap_micro_usd` and ignore it, which
 * is right — a field the server silently drops is worse than one that is absent,
 * since the operator who typed it believes it took effect. The surface shows the
 * artist's real budget from `/budgets` instead.
 *
 * Created as a draft that opens nothing. Running it stays a second, deliberate act.
 */
export function useCommitIntent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (plan: IntentPlan) =>
      post<{ id: string }>("/campaigns", {
        name: plan.name,
        artist_id: plan.artistId,
        channel: plan.channel,
        goal: plan.goal,
        recording_id: plan.recordingId ?? null,
      }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export interface ArtistRow {
  id: string;
  name: string;
  status: string;
  type: string;
  slug: string;
}

export function useArtists(): UseQueryResult<Listing<ArtistRow>, ApiError> {
  return useQuery({
    queryKey: ["artists"],
    queryFn: () => getListing<ArtistRow>("/artists"),
    staleTime: 60_000,
  });
}
