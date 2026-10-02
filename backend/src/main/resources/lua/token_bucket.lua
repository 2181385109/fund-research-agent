-- 双维度令牌桶（用户 + 全局），一次调用内原子完成「检查两个桶 → 都够才各扣 cost」。
-- 时间取 Redis 服务端的 TIME，多个 backend 实例之间不受本机时钟偏差影响。
-- KEYS[1] = 用户桶  KEYS[2] = 全局桶
-- ARGV    = 用户容量, 用户补充速率(个/秒), 全局容量, 全局补充速率(个/秒), cost
-- 返回    = {放行(1/0), 需要等待的毫秒数, 被拒的维度(0=无, 1=用户, 2=全局, 3=两者)}
-- 桶状态是 hash{tokens, ts}；不存在 = 满桶。被拒时不写任何状态（不会因为全局被拒而扣掉用户的令牌）。
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local cost = tonumber(ARGV[5])

local function level(key, cap, rate)
  local h = redis.call('HMGET', key, 'tokens', 'ts')
  local tokens = tonumber(h[1])
  local ts = tonumber(h[2])
  if tokens == nil or ts == nil then
    return cap
  end
  local delta = now - ts
  if delta < 0 then
    delta = 0
  end
  tokens = tokens + delta * rate / 1000
  if tokens > cap then
    tokens = cap
  end
  return tokens
end

local function wait_ms(tokens, rate)
  if tokens >= cost then
    return 0
  end
  if rate <= 0 then
    return 3600000
  end
  return math.ceil((cost - tokens) / rate * 1000)
end

local ucap, urate = tonumber(ARGV[1]), tonumber(ARGV[2])
local gcap, grate = tonumber(ARGV[3]), tonumber(ARGV[4])
local ut = level(KEYS[1], ucap, urate)
local gt = level(KEYS[2], gcap, grate)

if ut >= cost and gt >= cost then
  redis.call('HSET', KEYS[1], 'tokens', tostring(ut - cost), 'ts', tostring(now))
  redis.call('HSET', KEYS[2], 'tokens', tostring(gt - cost), 'ts', tostring(now))
  -- 空闲足够久后桶必然是满的，键可以过期（= 满桶）
  local ttl_u = (urate > 0) and math.ceil(ucap / urate * 1000) + 1000 or 3600000
  local ttl_g = (grate > 0) and math.ceil(gcap / grate * 1000) + 1000 or 3600000
  redis.call('PEXPIRE', KEYS[1], ttl_u)
  redis.call('PEXPIRE', KEYS[2], ttl_g)
  return {1, 0, 0}
end

local wu = wait_ms(ut, urate)
local wg = wait_ms(gt, grate)
local which = 0
if wu > 0 then which = which + 1 end
if wg > 0 then which = which + 2 end
return {0, math.max(wu, wg), which}
