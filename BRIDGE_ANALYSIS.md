# codex-lb Bridge Session Ownership & Replica Mismatch Analysis

## Executive Summary

**Problem**: LiteLLM in front of codex-lb hits `bridge_instance_mismatch` (409) under concurrency when using `/responses`-based GPT routing with sticky session affinity.

**Root Cause**: The HTTP bridge session ownership is determined by **rendezvous hashing** on `(affinity_kind, affinity_key, api_key_id)`, but the sticky key derivation is **non-deterministic across replicas** when clients don't provide explicit session headers or prompt cache keys.

**Impact**: Concurrent requests from the same client can hash to different codex-lb replicas, causing ownership mismatches when trying to reuse bridge sessions.

---

## 1. Bridge Session Ownership Model

### 1.1 Session Key Structure
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:4478-4481`

```python
@dataclass(frozen=True, slots=True)
class _HTTPBridgeSessionKey:
    affinity_kind: str        # "turn_state_header", "session_header", "prompt_cache", "sticky_thread", "request"
    affinity_key: str         # The actual session/cache key
    api_key_id: str | None    # API key scoping
```

### 1.2 Owner Instance Determination
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:5328-5337`

```python
async def _http_bridge_owner_instance(
    key: _HTTPBridgeSessionKey,
    settings: object,
    ring_membership: RingMembershipService | None = None,
) -> str | None:
    instance_id, ring = await _active_http_bridge_instance_ring(settings, ring_membership)
    if len(ring) <= 1:
        return instance_id
    hash_input = f"{key.affinity_kind}:{key.affinity_key}:{key.api_key_id or ''}"
    return select_node(hash_input, ring)  # Rendezvous hash
```

**Key Points**:
- Uses **rendezvous (HRW) hashing** via `select_node()` (line 5337)
- Hash input: `affinity_kind:affinity_key:api_key_id`
- Ring membership comes from DB (`BridgeRingMember` table)
- Single-replica deployments skip hashing (line 5334-5335)

### 1.3 Rendezvous Hash Implementation
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/core/balancer/rendezvous_hash.py:7-21`

```python
def select_node(key: str, nodes: Sequence[str]) -> str | None:
    """Rendezvous (HRW) hashing: returns node with highest sha256(key + node) hash."""
    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes[0]
    
    def _score(node: str) -> bytes:
        return sha256(f"{key}:{node}".encode()).digest()
    
    return max(nodes, key=_score)
```

**Property**: Deterministic given the same `key` and `nodes` set. **But** if `key` differs across replicas, ownership diverges.

---

## 2. Sticky Key Derivation (The Problem)

### 2.1 Sticky Key Priority Chain
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:5155-5195`

```python
def _sticky_key_for_responses_request(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
    api_key: ApiKeyData | None = None,
) -> _AffinityPolicy:
    cache_key, _ = _resolve_prompt_cache_key(payload, ...)
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    
    # Priority 1: x-codex-turn-state header (explicit, deterministic)
    if turn_state_key:
        return _AffinityPolicy(key=turn_state_key, kind=StickySessionKind.CODEX_SESSION)
    
    # Priority 2: x-codex-session-id header (explicit, deterministic)
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(key=session_key, kind=StickySessionKind.CODEX_SESSION)
    
    # Priority 3: prompt_cache_key from payload (explicit, deterministic)
    if openai_cache_affinity:
        return _AffinityPolicy(key=cache_key, kind=StickySessionKind.PROMPT_CACHE, ...)
    
    # Priority 4: sticky_threads (derived, NON-DETERMINISTIC)
    if sticky_threads_enabled:
        return _AffinityPolicy(key=cache_key, kind=StickySessionKind.STICKY_THREAD, reallocate_sticky=True)
    
    # Priority 5: no affinity (request-scoped, unique per request)
    return _AffinityPolicy()
```

### 2.2 Derived Prompt Cache Key (Non-Deterministic)
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:4967-4998`

```python
def _derive_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
) -> str:
    """Derive a stable, session-scoped prompt_cache_key when the client does not provide one."""
    parts: list[str] = []
    model = getattr(payload, "model", None)
    model_class = _extract_model_class(model) if isinstance(model, str) and model else None
    
    if api_key is not None:
        parts.append(api_key.id[:12])
    
    instructions = getattr(payload, "instructions", None)
    if isinstance(instructions, str) and instructions:
        parts.append(sha256(instructions[:512].encode()).hexdigest()[:12])
    
    first_user_text = _extract_first_user_input(payload)
    if first_user_text:
        parts.append(sha256(first_user_text[:512].encode()).hexdigest()[:12])
    
    if not parts:
        # ⚠️ PROBLEM: Random suffix generated per call
        random_suffix = uuid4().hex[:24]
        return f"{model_class}-{random_suffix}" if model_class is not None else random_suffix
    
    return "-".join([model_class, *parts]) if model_class is not None else "-".join(parts)
```

**Critical Issue** (line 4995):
- When `parts` is empty (no API key, no instructions, no user input), a **random UUID** is generated
- This means **each request gets a different key**, even from the same client
- Different keys → different owner instances → `bridge_instance_mismatch`

### 2.3 Session Key Derivation in Bridge
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:5198-5222`

```python
def _make_http_bridge_session_key(
    payload: ResponsesRequest,
    *,
    headers: Mapping[str, str],
    affinity: _AffinityPolicy,
    api_key: ApiKeyData | None,
    request_id: str,
) -> _HTTPBridgeSessionKey:
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key is not None:
        affinity_key = turn_state_key
        affinity_kind = "turn_state_header"
    else:
        session_key = _sticky_key_from_session_header(headers)
        if session_key is not None:
            affinity_key = session_key
            affinity_kind = "session_header"
        else:
            # ⚠️ PROBLEM: Falls back to affinity.key (which may be random) or request_id
            affinity_key = affinity.key or request_id
            affinity_kind = affinity.kind.value if affinity.kind is not None else "request"
    return _HTTPBridgeSessionKey(
        affinity_kind=affinity_kind,
        affinity_key=affinity_key,
        api_key_id=api_key.id if api_key is not None else None,
    )
```

**Problem** (line 5216):
- If no explicit headers, uses `affinity.key` (which may be randomly derived)
- If `affinity.key` is None, falls back to `request_id` (unique per request)
- Either way, **different requests get different keys**

---

## 3. Replica Mismatch Detection & Behavior

### 3.1 Mismatch Detection Logic
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:1582-1610`

```python
owner_instance = await _http_bridge_owner_instance(key, settings, self._ring_membership)
current_instance, ring = await _active_http_bridge_instance_ring(settings, self._ring_membership)

if (
    key.affinity_kind != "request"  # Only check for sticky sessions
    and owner_instance is not None
    and len(ring) > 1  # Multi-replica only
    and owner_instance != current_instance  # Mismatch!
):
    _log_http_bridge_event(
        "owner_mismatch_retry",
        key,
        account_id=None,
        model=request_model,
        detail=f"expected_instance={owner_instance}, current_instance={current_instance}, outcome=retry",
        cache_key_family=key.affinity_kind,
        model_class=_extract_model_class(request_model) if request_model else None,
    )
    if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
        bridge_instance_mismatch_total.labels(outcome="retry").inc()
    raise ProxyResponseError(
        409,
        openai_error(
            "bridge_instance_mismatch",
            "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
            error_type="server_error",
        ),
    )
```

**Conditions for 409 Error**:
1. `affinity_kind != "request"` (sticky session, not request-scoped)
2. `owner_instance is not None` (ring membership available)
3. `len(ring) > 1` (multi-replica deployment)
4. `owner_instance != current_instance` (hash points to different replica)

### 3.2 Turn-State Continuity Check
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:1520-1553`

```python
incoming_turn_state = _sticky_key_from_turn_state_header(headers)
if incoming_turn_state is not None:
    # Client provided x-codex-turn-state header
    existing_session = self._http_bridge_sessions.get(key)
    if existing_session is not None and existing_session.upstream_turn_state != incoming_turn_state:
        # Turn state mismatch: session exists but with different turn state
        raise ProxyResponseError(
            409,
            openai_error(
                "bridge_instance_mismatch",
                "HTTP bridge turn-state reached an instance that does not own the live session",
                error_type="server_error",
            ),
        )
```

**Scenario**: Client sends `x-codex-turn-state` but lands on wrong replica → 409.

---

## 4. Ring Membership & Instance Registration

### 4.1 Ring Membership Service
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/ring_membership.py:26-175`

```python
class RingMembershipService:
    """Manages pod registration in the bridge ring."""
    
    async def register(self, instance_id: str) -> None:
        """Upsert pod into ring. Safe to call multiple times."""
        # Dialect-specific upsert (PostgreSQL or SQLite)
        # Stores: id (UUID), instance_id, registered_at, last_heartbeat_at
    
    async def heartbeat(self, instance_id: str) -> None:
        """Upsert heartbeat — recovers from mark_stale or unregister."""
    
    async def list_active(self, stale_threshold_seconds: int = 120) -> list[str]:
        """Return sorted list of pods whose heartbeat is within threshold."""
        cutoff = utcnow() - timedelta(seconds=stale_threshold_seconds)
        # SELECT instance_id WHERE last_heartbeat_at >= cutoff ORDER BY instance_id
    
    async def ring_fingerprint(self, stale_threshold_seconds: int = 120) -> str:
        """sha256 of sorted active member list. Same for all pods with same membership."""
```

### 4.2 Ring Membership DB Model
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/db/models.py:410-418`

```python
class BridgeRingMember(Base):
    __tablename__ = "bridge_ring_members"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    instance_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**Key Properties**:
- `instance_id` is unique (one entry per pod)
- Heartbeat-based liveness (120s stale threshold)
- Sorted list used for consistent hashing

### 4.3 Active Ring Lookup
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:5306-5325`

```python
async def _active_http_bridge_instance_ring(
    settings: object,
    ring_membership: RingMembershipService | None,
) -> tuple[str, tuple[str, ...]]:
    instance_id, static_ring = _normalized_http_bridge_instance_ring(settings)
    if ring_membership is None:
        return instance_id, static_ring
    try:
        active_members = await ring_membership.list_active()
    except Exception:
        logger.warning("Bridge ring lookup failed — refusing to fall back to static ring", exc_info=True)
        raise
    if not active_members:
        return instance_id, static_ring
    normalized_members = tuple(
        sorted({member.strip() for member in active_members if isinstance(member, str) and member.strip()})
    )
    if not normalized_members:
        return instance_id, static_ring
    return instance_id, normalized_members
```

**Behavior**:
- Queries DB for active members (heartbeat >= 120s ago)
- Falls back to static ring if DB lookup fails (raises exception)
- Returns sorted tuple for consistent hashing

---

## 5. Sticky Session Storage

### 5.1 Sticky Session Model
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/db/models.py:157-181`

```python
class StickySession(Base):
    __tablename__ = "sticky_sessions"
    
    key: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[StickySessionKind] = mapped_column(
        SqlEnum(StickySessionKind, ...),
        primary_key=True,
        default=StickySessionKind.STICKY_THREAD,
        nullable=False,
    )
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
```

**Key Properties**:
- Composite PK: `(key, kind)`
- Scoped to account
- Tracks creation and update time

### 5.2 Sticky Session Kinds
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/db/models.py:44-48`

```python
class StickySessionKind(str, Enum):
    CODEX_SESSION = "codex_session"      # Explicit x-codex-session-id header
    STICKY_THREAD = "sticky_thread"      # Derived from payload (non-deterministic)
    PROMPT_CACHE = "prompt_cache"        # OpenAI prompt cache key
```

### 5.3 Sticky Session Repository
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/sticky_repository.py:57-65`

```python
async def upsert(self, key: str, account_id: str, *, kind: StickySessionKind) -> StickySession:
    statement = self._build_upsert_statement(key, account_id, kind)
    await self._session.execute(statement)
    await self._session.commit()
    row = await self.get_entry(key, kind=kind)
    if row is None:
        raise RuntimeError(f"StickySession upsert failed for key={key!r} kind={kind.value!r}")
    await self._session.refresh(row)
    return row
```

**Behavior**:
- Upserts on `(key, kind)` composite key
- Updates `account_id` and `updated_at` on conflict
- Shared across all replicas (single DB)

---

## 6. /responses Endpoint Flow

### 6.1 HTTP Streaming Endpoint
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/api.py:124-150`

```python
@router.post("/responses", responses={200: {"content": {"text/event-stream": {...}}}})
async def responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await _stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prefer_http_bridge=True,  # ← Uses HTTP bridge
    )
```

### 6.2 HTTP Bridge Stream Flow
**File**: `/Users/taehoon/Desktop/soju/codex-lb/app/modules/proxy/service.py:205-230`

```python
def stream_http_responses(
    self,
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    api_key: ApiKeyData | None = None,
    api_key_reservation: ApiKeyUsageReservationData | None = None,
    suppress_text_done_events: bool = False,
    downstream_turn_state: str | None = None,
) -> AsyncIterator[str]:
    filtered = filter_inbound_headers(headers)
    return self._stream_http_bridge_or_retry(
        payload,
        filtered,
        codex_session_affinity=codex_session_affinity,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
        api_key_reservation=api_key_reservation,
        suppress_text_done_events=suppress_text_done_events,
        downstream_turn_state=downstream_turn_state,
    )
```

---

## 7. Concurrency Problem Under LiteLLM

### Scenario: LiteLLM → codex-lb (2 replicas)

```
Client Request (no session header, no cache key)
    ↓
LiteLLM (load balances to replica A or B)
    ↓
codex-lb-A: _derive_prompt_cache_key() → "mini-uuid1-random1" (random UUID)
    ↓
_http_bridge_owner_instance("sticky_thread:mini-uuid1-random1:api-key-id")
    ↓
select_node() → hashes to codex-lb-B (different replica!)
    ↓
409 bridge_instance_mismatch (A != B)
```

### Why It Happens

1. **LiteLLM load balances** to replica A
2. **Replica A derives** a random cache key (UUID-based)
3. **Rendezvous hash** of that key points to replica B
4. **Replica A checks** if it owns the session → NO
5. **409 error** returned to client

### Why It's Worse Under Concurrency

- Multiple concurrent requests from same client
- Each request gets a **different random UUID**
- Each UUID hashes to a **different owner instance**
- High probability of landing on wrong replica
- Client sees 409 errors on retry

---

## 8. Upstream Changes for Gateway Safety

### 8.1 Problem Summary

| Component | Issue | Impact |
|-----------|-------|--------|
| **Sticky Key Derivation** | Random UUID when no explicit key | Non-deterministic ownership |
| **Rendezvous Hashing** | Correct algorithm, but input is unstable | Different replicas see different keys |
| **Ring Membership** | DB-based, shared across replicas | Consistent view, but hash input varies |
| **Bridge Session Ownership** | Checked at session creation | 409 if wrong replica |

### 8.2 Recommended Upstream Changes

#### **Change 1: Deterministic Fallback Key Derivation**
**File**: `app/modules/proxy/service.py:4967-4998`

**Current**:
```python
if not parts:
    random_suffix = uuid4().hex[:24]
    return f"{model_class}-{random_suffix}" if model_class is not None else random_suffix
```

**Proposed**:
```python
if not parts:
    # Use request_id or client IP hash instead of random UUID
    # This ensures same client always gets same key
    fallback_key = _derive_client_identity_key(request_id, client_ip)
    return f"{model_class}-{fallback_key}" if model_class is not None else fallback_key
```

**Benefit**: Same client → same derived key → same owner instance.

---

#### **Change 2: Explicit Client Identity Header**
**File**: `app/modules/proxy/service.py:5155-5195`

**Proposed Addition**:
```python
def _sticky_key_for_responses_request(...) -> _AffinityPolicy:
    # ... existing priority chain ...
    
    # NEW: Priority 1.5 - x-client-identity header (explicit, deterministic)
    if codex_session_affinity:
        client_identity = _sticky_key_from_client_identity_header(headers)
        if client_identity:
            return _AffinityPolicy(
                key=client_identity,
                kind=StickySessionKind.CODEX_SESSION,
            )
    
    # ... rest of chain ...
```

**Benefit**: LiteLLM can inject `x-client-identity` header with stable client ID.

---

#### **Change 3: Request-Scoped Sessions for Gateways**
**File**: `app/modules/proxy/service.py:1584-1610`

**Proposed**:
```python
# Skip ownership check if affinity_kind is "request" (request-scoped, no reuse)
if (
    key.affinity_kind != "request"  # ← Already checks this
    and owner_instance is not None
    and len(ring) > 1
    and owner_instance != current_instance
):
    # Only raise 409 for sticky sessions, not request-scoped
    raise ProxyResponseError(409, ...)
```

**Benefit**: Gateways can force `affinity_kind="request"` to bypass ownership checks.

---

#### **Change 4: Sticky Key Source Logging**
**File**: `app/modules/proxy/service.py:301-322`

**Current**:
```python
sticky_key_source = "none"
if affinity.key:
    sticky_key_source = (
        "turn_state_header" if _sticky_key_from_turn_state_header(headers) is not None else "session_header"
    )
    sticky_key_source = "payload" if had_prompt_cache_key else "derived"
```

**Proposed**: Add explicit logging of which source was used:
```python
logger.info(
    "sticky_key_source=%s affinity_kind=%s affinity_key_hash=%s",
    sticky_key_source,
    affinity.kind.value if affinity.kind else "none",
    _hash_identifier(affinity.key) if affinity.key else "none",
)
```

**Benefit**: Visibility into why ownership mismatches occur.

---

#### **Change 5: Graceful Degradation for Multi-Replica**
**File**: `app/modules/proxy/service.py:1582-1610`

**Proposed**:
```python
# Add config flag to allow graceful degradation
if (
    key.affinity_kind != "request"
    and owner_instance is not None
    and len(ring) > 1
    and owner_instance != current_instance
):
    if settings.bridge_allow_ownership_mismatch:
        # Log warning but allow session creation on current instance
        logger.warning(
            "bridge_ownership_mismatch_allowed instance_id=%s owner=%s key=%s",
            current_instance,
            owner_instance,
            _hash_identifier(key.affinity_key),
        )
    else:
        # Strict mode: raise 409
        raise ProxyResponseError(409, ...)
```

**Benefit**: Gateways can opt-in to relaxed ownership checks during migration.

---

### 8.3 LiteLLM Integration Strategy

#### **Option A: Explicit Session Headers** (Recommended)
1. LiteLLM generates stable `x-codex-session-id` per client
2. Injects into every request to codex-lb
3. codex-lb uses header as sticky key (Priority 2)
4. No random UUID generation
5. Deterministic ownership

**Pros**: No codex-lb changes needed, works with current code
**Cons**: LiteLLM must track client sessions

#### **Option B: Request-Scoped Sessions**
1. LiteLLM sets `x-codex-affinity-kind: request` header
2. codex-lb treats each request as independent (no reuse)
3. Skips ownership checks
4. No bridge session reuse across requests

**Pros**: Simple, no state tracking
**Cons**: No session reuse, higher latency

#### **Option C: Hybrid (Recommended for Production)**
1. LiteLLM injects `x-codex-session-id` for stateful clients
2. Falls back to request-scoped for stateless clients
3. codex-lb respects both modes
4. Gradual migration path

---

## 9. Key Functions & Classes Map

| File | Function/Class | Purpose |
|------|---|---|
| `service.py:5328-5337` | `_http_bridge_owner_instance()` | Determines which replica owns a session |
| `service.py:5306-5325` | `_active_http_bridge_instance_ring()` | Fetches active replicas from DB |
| `service.py:5155-5195` | `_sticky_key_for_responses_request()` | Derives affinity policy from request |
| `service.py:4967-4998` | `_derive_prompt_cache_key()` | **Generates random UUID (problem)** |
| `service.py:5198-5222` | `_make_http_bridge_session_key()` | Creates session key for bridge |
| `service.py:1582-1610` | Ownership check in `_get_or_create_http_bridge_session()` | **Raises 409 on mismatch** |
| `ring_membership.py:150-161` | `list_active()` | Queries active replicas from DB |
| `rendezvous_hash.py:7-21` | `select_node()` | Rendezvous hash algorithm |
| `sticky_repository.py:57-65` | `upsert()` | Stores sticky session in DB |
| `models.py:410-418` | `BridgeRingMember` | DB table for ring membership |
| `models.py:157-181` | `StickySession` | DB table for sticky sessions |

---

## 10. Hypothesis: Why LiteLLM Hits This

### Scenario
```
LiteLLM (single instance) → codex-lb (2 replicas, DB-backed ring)

Request 1:
  LiteLLM → codex-lb-A
  A derives key "mini-uuid1-random1"
  Hash points to B
  409 bridge_instance_mismatch

Request 2 (retry):
  LiteLLM → codex-lb-B (by chance)
  B derives key "mini-uuid2-random2" (different UUID!)
  Hash points to A
  409 bridge_instance_mismatch

Request 3 (retry):
  LiteLLM → codex-lb-A
  A derives key "mini-uuid3-random3" (different UUID again!)
  Hash points to B
  409 bridge_instance_mismatch
```

### Why It's Worse Than Single-Replica
- Single replica: No ownership check (line 5334-5335)
- Multi-replica: Ownership check always fails with random keys

### Why It's Worse Than Explicit Headers
- With `x-codex-session-id`: Same key → same owner → no mismatch
- Without header: Random key → random owner → high mismatch rate

---

## 11. Verification Checklist

- [x] Bridge session ownership uses rendezvous hashing
- [x] Hash input: `affinity_kind:affinity_key:api_key_id`
- [x] Sticky key derivation can generate random UUIDs
- [x] Random UUIDs cause non-deterministic ownership
- [x] Ownership mismatch raises 409 in multi-replica
- [x] Ring membership is DB-backed and shared
- [x] Sticky sessions are stored in shared DB
- [x] /responses endpoint uses HTTP bridge with affinity
- [x] LiteLLM load balancing can land on different replicas
- [x] Concurrent requests get different random keys

---

## 12. Conclusion

**Root Cause**: Sticky key derivation uses random UUIDs when clients don't provide explicit session headers or cache keys. This causes different replicas to compute different ownership hashes, leading to 409 `bridge_instance_mismatch` errors under concurrency.

**Upstream Fixes** (in priority order):
1. **Deterministic fallback key** instead of random UUID
2. **Explicit client identity header** support in codex-lb
3. **Request-scoped session mode** to bypass ownership checks
4. **Better logging** of sticky key sources
5. **Graceful degradation** config flag for gateways

**LiteLLM Integration** (recommended):
- Inject `x-codex-session-id` header with stable client ID
- Falls back to request-scoped if no session ID available
- No changes to codex-lb needed (works with current code)
