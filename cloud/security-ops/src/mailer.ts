// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Outbound alert abstraction. Two surfaces today:
//
//   1. message.reply()  — auto-ack on the inbound side, reuses the
//                          ForwardableEmailMessage's reply primitive.
//                          The reporter sees the ack in the same thread.
//
//   2. sendOpsAlert()    — operator alert (urgent / overdue digest /
//                          forwarded-report tip-off). Used by both the
//                          email handler (urgent path) and the cron
//                          handler. Built on MailChannels, which is
//                          free for Workers and signs FROM-domain mail
//                          via DNS without an API key.
//
// Designed so a future PR can add a `sendSlackAlert()` or
// `sendTelegramAlert()` and have the email-handler + cron-handler
// fan out across all configured sinks. The interface here is the
// natural seam: a single function with (subject, body) params.
// See the AlertSink type below.

import type { Env } from './env';

/**
 * Future expansion point: each alert sink implements this. Today we
 * only have one (email via MailChannels), but designing the seam now
 * means a Slack/Telegram add-on is a 30-minute PR rather than a
 * refactor.
 */
export interface AlertSink {
  name: string;
  send(env: Env, msg: AlertMessage): Promise<void>;
}

export interface AlertMessage {
  subject: string;
  text: string;
}

/**
 * Send a notification to the operator. Today this is one sink
 * (email); the function exists so callers don't pin themselves to
 * the email API directly.
 */
export async function sendOpsAlert(env: Env, msg: AlertMessage): Promise<void> {
  await emailSink.send(env, msg);
}

// ---------------------------------------------------------------------------
// MailChannels email sink
// ---------------------------------------------------------------------------
//
// MailChannels' free tier signs mail from Cloudflare Workers without an
// API key, as long as the From-domain has the right DNS records. See
// https://api.mailchannels.net/tx/v1/send.
//
// In test (vitest-pool-workers / miniflare) this fetch is intercepted
// by the override in vitest.config.mts — the global fetch is replaced
// per-test so we can assert what we'd have sent without actually
// reaching out to the network. See test/_helpers.ts.

const MAILCHANNELS_ENDPOINT = 'https://api.mailchannels.net/tx/v1/send';

export const emailSink: AlertSink = {
  name: 'email',
  async send(env: Env, msg: AlertMessage): Promise<void> {
    if (!env.SECURITY_FORWARD_TO) {
      // Misconfigured deploy — fail loud in logs but don't throw,
      // so the rest of the email-handler pipeline (D1 insert, ack)
      // is unaffected.
      console.error(
        JSON.stringify({
          level: 'error',
          msg: 'sendOpsAlert_no_forward_address',
          env: env.ENVIRONMENT,
        }),
      );
      return;
    }
    const body = {
      personalizations: [{ to: [{ email: env.SECURITY_FORWARD_TO }] }],
      from: {
        email: env.SECURITY_FROM_ADDRESS,
        name: 'B-Tree Labs Security',
      },
      subject: msg.subject,
      content: [{ type: 'text/plain', value: msg.text }],
    };
    const res = await fetch(MAILCHANNELS_ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => '<no body>');
      console.error(
        JSON.stringify({
          level: 'error',
          msg: 'mailchannels_send_failed',
          status: res.status,
          detail: detail.slice(0, 500),
          env: env.ENVIRONMENT,
        }),
      );
      throw new Error(`mailchannels_send_failed:${res.status}`);
    }
  },
};
