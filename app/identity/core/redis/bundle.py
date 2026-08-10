from __future__ import annotations

import json
from dataclasses import dataclass

from redis.asyncio import Redis

# Field expiry is carried inside the document rather than by per-key TTLs, so a
# single SET renews the whole bundle. `now` is passed in from the client: Redis
# forbids non-deterministic calls in scripts that write.
_BUNDLE_LUA = """
local now = tonumber(ARGV[1])
local ops = cjson.decode(ARGV[2])

local states = {}
local loaded = {}
local dirty = {}

local function state_for(i)
  if not loaded[i] then
    local s = {}
    local raw = redis.call('GET', KEYS[i])
    if raw then
      local ok, decoded = pcall(cjson.decode, raw)
      if ok and type(decoded) == 'table' then
        for f, entry in pairs(decoded) do
          if type(entry) == 'table' and entry.e and tonumber(entry.e) > now then
            s[f] = entry
          end
        end
      end
    end
    states[i] = s
    loaded[i] = true
  end
  return states[i]
end

local results = {}

for idx = 1, #ops do
  local op = ops[idx]
  local s = state_for(op.k)
  local res = {}

  if op.o == 'incr' then
    -- The window is anchored to the first hit so a burst cannot slide it forward.
    local cur = s[op.f]
    local count = 1
    local expires = now + op.w
    if cur and cur.c then
      count = cur.c + 1
      expires = tonumber(cur.e)
    end
    s[op.f] = { c = count, e = expires }
    dirty[op.k] = true
    res.c = count
    res.a = (count <= op.l) and 1 or 0
  elseif op.o == 'get' then
    local cur = s[op.f]
    if cur and cur.v then res.v = cur.v end
  elseif op.o == 'set' then
    s[op.f] = { v = op.v, e = now + op.w }
    dirty[op.k] = true
  elseif op.o == 'del' then
    if s[op.f] ~= nil then
      s[op.f] = nil
      dirty[op.k] = true
    end
  end

  results[idx] = res
end

for i in pairs(dirty) do
  local s = states[i]
  local max_e = 0
  local n = 0
  for _, entry in pairs(s) do
    n = n + 1
    local e = tonumber(entry.e)
    if e > max_e then max_e = e end
  end
  if n == 0 then
    redis.call('DEL', KEYS[i])
  else
    local ttl = math.ceil(max_e - now)
    if ttl < 1 then ttl = 1 end
    redis.call('SET', KEYS[i], cjson.encode(s), 'EX', ttl)
  end
end

if #results == 0 then return '[]' end
return cjson.encode(results)
"""


@dataclass(frozen=True, slots=True)
class Increment:
    """Bump a counter field and report whether it is still within `limit`."""

    key: str
    field: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class ReadValue:
    key: str
    field: str


@dataclass(frozen=True, slots=True)
class WriteValue:
    key: str
    field: str
    value: str
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class DeleteValue:
    key: str
    field: str


Op = Increment | ReadValue | WriteValue | DeleteValue


@dataclass(frozen=True, slots=True)
class CounterResult:
    count: int
    allowed: bool


class BundleStore:
    """Executes a batch of bundle ops as one Redis command."""

    def __init__(self, redis: Redis) -> None:
        # register_script sends EVALSHA and only replays the body on NOSCRIPT,
        # so the script text is not re-uploaded on every call.
        self._script = redis.register_script(_BUNDLE_LUA)

    async def execute(self, ops: list[Op], *, now: int) -> list[CounterResult | str | None]:
        if not ops:
            return []

        # Ops address keys by index because Redis requires every touched key in KEYS.
        key_index: dict[str, int] = {}
        keys: list[str] = []
        encoded: list[dict[str, object]] = []

        for op in ops:
            idx = key_index.get(op.key)
            if idx is None:
                keys.append(op.key)
                idx = len(keys)
                key_index[op.key] = idx
            encoded.append(_encode(op, idx))

        raw = await self._script(keys=keys, args=[now, json.dumps(encoded)])
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        return [_decode(op, entry) for op, entry in zip(ops, payload, strict=True)]


def _encode(op: Op, key_index: int) -> dict[str, object]:
    match op:
        case Increment():
            return {
                "k": key_index,
                "f": op.field,
                "o": "incr",
                "l": op.limit,
                "w": op.window_seconds,
            }
        case ReadValue():
            return {"k": key_index, "f": op.field, "o": "get"}
        case WriteValue():
            return {
                "k": key_index,
                "f": op.field,
                "o": "set",
                "v": op.value,
                "w": op.ttl_seconds,
            }
        case DeleteValue():
            return {"k": key_index, "f": op.field, "o": "del"}


def _decode(op: Op, entry: object) -> CounterResult | str | None:
    # An op with nothing to report returns an empty Lua table, which cjson
    # renders as `[]` rather than `{}` — so anything not a dict means "empty".
    fields: dict[str, object] = entry if isinstance(entry, dict) else {}
    match op:
        case Increment():
            return CounterResult(count=int(fields.get("c", 0)), allowed=bool(fields.get("a", 0)))
        case ReadValue():
            value = fields.get("v")
            return str(value) if value is not None else None
        case _:
            return None
