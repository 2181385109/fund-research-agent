-- 每日配额：检查「今日已用次数 / token」，未超限则次数 +1（原子）。
-- KEYS[1] = 今日计数 hash{calls, tokens}  KEYS[2] = 待回写 MySQL 的 set（成员 "userId:yyyyMMdd"）
-- ARGV    = 次数上限(<=0 不限), token 上限(<=0 不限), 计数键的存活秒数, 回写 set 的成员
-- 返回    = {放行(1/0), 被拒原因(0=无, 1=次数, 2=token), 今日次数, 今日 token}
-- token 在对话结束后才知道，所以 token 上限是「开始前检查已用量」的软上限：最后一次请求可以越过它。
local calls = tonumber(redis.call('HGET', KEYS[1], 'calls') or '0')
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or '0')
local max_calls = tonumber(ARGV[1])
local max_tokens = tonumber(ARGV[2])

if max_calls > 0 and calls >= max_calls then
  return {0, 1, calls, tokens}
end
if max_tokens > 0 and tokens >= max_tokens then
  return {0, 2, calls, tokens}
end

calls = redis.call('HINCRBY', KEYS[1], 'calls', 1)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call('SADD', KEYS[2], ARGV[4])
return {1, 0, calls, tokens}
