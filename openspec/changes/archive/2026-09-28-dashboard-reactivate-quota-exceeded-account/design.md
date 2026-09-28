# Design

## Context

Usage history stores upstream slots verbatim. A weekly-only Professional account can therefore have a 10,080-minute row under `primary` and no `secondary` row. Dashboard mapping and foreground routing already interpret that row as weekly, while usage refresh recovery expected a separate long-window slot. Replica-local runtime state can also outlive a database status repair.

## Goals / Non-Goals

**Goals:** Use the weekly-only interpretation consistently in status recovery and routing; clear stale local quota backoff after causal evidence of recovery; retain Team short-window holds and sticky ownership.

**Non-Goals:** Change the stored usage slot, create synthetic short-window quota, or bypass a genuine upstream quota rejection.

## Decisions

- Normalize a weekly-only upstream primary sample inside usage status recovery, without changing its persistence. The alternative of rewriting history would make storage disagree with upstream and affect other consumers.
- Track which replica-local block came from a quota rejection. Clear that block only when the database account is active and a recent available weekly sample was recorded after the block. The alternative of clearing every runtime cooldown on ACTIVE status could erase a separate rate-limit hold.
- Retain the existing reactivation endpoint and expose it for quota-exceeded dashboard accounts. Recovery remains automatic after the quota cooldown and fresh usage; the manual path is an operator retry.

## Risks / Trade-offs

- A weekly sample collected before a quota rejection does not prove recovery. The post-block timestamp gate intentionally keeps that account blocked until fresh evidence arrives.
- A genuine upstream exhaustion after recovery re-marks the account unavailable through the existing quota rejection path.

## Migration Plan

No schema migration is required. Deploying the service enables recovery on the next successful usage refresh; rolling back restores the previous recovery behavior without changing stored history.
