-- Atomically marks one event as seen (dedup) and merges its statistics into one packed
-- bucket per resolution. Each bucket is a single hash field holding a JSON object
-- {stat: "value"} (numbers as %.17g strings), where first/last values are ["ts", "seq", "value"]. One read and one write per
-- bucket keeps the number of Redis calls per event constant (instead of one per statistic).
--
-- KEYS[1] dedup key            dedup:{vehicle_id}:{seq}
-- KEYS[2] meta hash            {prefix}:{vid}:meta
-- KEYS[3..] for each bucket resolution r: hash key, then zset index key
--
-- ARGV[1] dedup ttl (s)        ARGV[2] state ttl (s)
-- ARGV[3] event ts (ms)        ARGV[4] seq
-- ARGV[5] floor-to-snapshot ts (ms) of the event, used to initialise last_snapshot_ts
-- ARGV[6] number of resolutions R
-- ARGV[7 .. 6+R] bucket index for each resolution (same order as KEYS)
-- ARGV[7+R] JSON object {stat: [op, value]}; op in c (count), s (sum), mn, mx, f, l
-- Returns 1 if applied, 0 if the dedup key already existed (nothing changed).

-- Everything that can fail on bad input happens before the first write: Redis does not roll
-- back a script that errors midway.
-- Stored numbers are strings formatted with %.17g, which round-trip IEEE doubles exactly
-- (cjson's own number encoder is limited to 14 significant digits, too few for sum/sumsq).
local contrib = cjson.decode(ARGV[7 + tonumber(ARGV[6])])
local function fmt(x) return string.format('%.17g', x) end

if not redis.call('SET', KEYS[1], '1', 'NX', 'EX', tonumber(ARGV[1])) then
  return 0
end

local state_ttl = tonumber(ARGV[2])
local ts = tonumber(ARGV[3])
local seq = tonumber(ARGV[4])
local R = tonumber(ARGV[6])

local function later(a_ts, a_seq, b_ts, b_seq)
  return (a_ts > b_ts) or (a_ts == b_ts and a_seq > b_seq)
end

for r = 1, R do
  local hkey = KEYS[1 + 2 * r]
  local zkey = KEYS[2 + 2 * r]
  local idx = ARGV[6 + r]
  local raw = redis.call('HGET', hkey, idx)
  local b = raw and cjson.decode(raw) or {}
  for stat, pair in pairs(contrib) do
    local op, v = pair[1], pair[2]
    local cur = b[stat]
    if op == 'c' or op == 's' then
      b[stat] = fmt((cur and tonumber(cur) or 0) + v)
    elseif op == 'mn' then
      if cur == nil or v < tonumber(cur) then b[stat] = fmt(v) end
    elseif op == 'mx' then
      if cur == nil or v > tonumber(cur) then b[stat] = fmt(v) end
    elseif op == 'f' then
      if cur == nil or later(tonumber(cur[1]), tonumber(cur[2]), ts, seq) then b[stat] = {fmt(ts), fmt(seq), fmt(v)} end
    elseif op == 'l' then
      if cur == nil or later(ts, seq, tonumber(cur[1]), tonumber(cur[2])) then b[stat] = {fmt(ts), fmt(seq), fmt(v)} end
    end
  end
  redis.call('HSET', hkey, idx, cjson.encode(b))
  redis.call('ZADD', zkey, idx, idx)
  redis.call('EXPIRE', hkey, state_ttl)
  redis.call('EXPIRE', zkey, state_ttl)
end

local meta = KEYS[2]
local cur_max = redis.call('HGET', meta, 'max_event_ts')
if (not cur_max) or ts > tonumber(cur_max) then
  redis.call('HSET', meta, 'max_event_ts', ARGV[3])
end
local cur_min = redis.call('HGET', meta, 'first_event_ts')
if (not cur_min) or ts < tonumber(cur_min) then
  redis.call('HSET', meta, 'first_event_ts', ARGV[3])
end
redis.call('HSETNX', meta, 'last_snapshot_ts', ARGV[5])
redis.call('HINCRBY', meta, 'events_applied', 1)
redis.call('EXPIRE', meta, state_ttl)
return 1
