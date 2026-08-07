-- Minimal fixture: one tenant, one artist, one track, two channels, two
-- campaigns, one counterparty who is BOTH a creator and a curator.
-- Exists to exercise PLATFORM-SPEC §3c — the cross-channel collision.

INSERT INTO tenant (id, slug, name) VALUES
  ('11111111-1111-1111-1111-111111111111', 'rtf', 'Respect the Funk');

INSERT INTO artist (id, tenant_id, slug, name) VALUES
  ('22222222-2222-2222-2222-222222222222',
   '11111111-1111-1111-1111-111111111111', 'test-artist', 'Test Artist');

INSERT INTO track (id, tenant_id, artist_id, title) VALUES
  ('33333333-3333-3333-3333-333333333333',
   '11111111-1111-1111-1111-111111111111',
   '22222222-2222-2222-2222-222222222222', 'Test Track');

INSERT INTO channel_playbook
  (tenant_id, channel, counterparty_kind, state_machine, cadence,
   draft_system_prompt, success_metric) VALUES
  ('11111111-1111-1111-1111-111111111111', 'ugc',   'creator', '{}', '{}', 'x', 'posts'),
  ('11111111-1111-1111-1111-111111111111', 'radio', 'radio',   '{}', '{}', 'x', 'spins');

INSERT INTO campaign (id, tenant_id, artist_id, track_id, channel, state) VALUES
  ('44444444-4444-4444-4444-444444444444',
   '11111111-1111-1111-1111-111111111111',
   '22222222-2222-2222-2222-222222222222',
   '33333333-3333-3333-3333-333333333333', 'ugc', 'active'),
  ('55555555-5555-5555-5555-555555555555',
   '11111111-1111-1111-1111-111111111111',
   '22222222-2222-2222-2222-222222222222',
   '33333333-3333-3333-3333-333333333333', 'radio', 'active');

-- One person, reachable through two different channels. This is the case that
-- makes running channels in parallel dangerous.
INSERT INTO counterparty
  (id, tenant_id, kind, platform, platform_user_id, handle, contact_state) VALUES
  ('66666666-6666-6666-6666-666666666666',
   '11111111-1111-1111-1111-111111111111', 'creator', 'tiktok', 'u123',
   '@doubleagent', 'contactable');
