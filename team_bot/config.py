from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import os

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    token: str = ''
    bot_username: str = ''
    key: str = ''
    model: str = ''
    owner: int = 0
    chat: int = 0
    timezone: str = 'Asia/Shanghai'
    data: Path = ROOT / 'data'
    backups: Path = ROOT / 'data/backups'
    threads: dict = field(default_factory=lambda: dict(progress=0, training=0, management=0, report=0))
    times: dict = field(default_factory=lambda: dict(am='12:00', am_overdue='13:00', pm='18:00', pm_overdue='19:00', open='18:00', deadline='21:00', summary='21:15'))
    workdays: tuple = (0, 1, 2, 3, 4)
    catchup: int = 120
    repost_delay: int = 180
    ai_enabled: bool = False
    free_confirmed: bool = False
    budget: int = 20
    queue_size: int = 10
    concurrency: int = 1
    timeout: int = 20
    retries: int = 2
    output_tokens: int = 600
    cache_seconds: int = 3600
    max_file_mb: int = 10
    retention: int = 14
    shifts: dict = field(default_factory=lambda: dict(am_start='12:00', am_end='17:00', pm_start='19:00', pm_end='00:00'))
    offsets: dict = field(default_factory=lambda: dict(open=0, deadline=0, summary=0))
    report_times_confirmed: bool = False
    test_run: str = ''
    training_mode: str = 'qa'
    report_version: int = 1
    click_cooldown: int = 2
    identity_mode: str = 'legacy'
    membership_required: bool = False
    internal_chat: int = 0
    run_mode: str = 'legacy'
    trial_label: str = '【验收试用，不计正式数据】'
    schedule_allowed: bool = True

    @property
    def zone(self):
        return ZoneInfo(self.timezone)

    def now(self):
        return datetime.now(self.zone)

    def business_date(self, now=None):
        now = (now or self.now()).astimezone(self.zone)
        if self.report_version >= 2:
            return now.date()
        # 下半场在午夜结束；下一次上半场开始前仍沿用上一工作日，便于午夜补交。
        return now.date() - timedelta(days=int(now.time().replace(tzinfo=None) < time.fromisoformat(self.shifts['am_start'])))

    def event_time(self, day, kind):
        return datetime.combine(day + timedelta(days=self.offsets.get(kind, 0)), time.fromisoformat(self.times[kind]), self.zone)

    def validate(self):
        if self.training_mode not in ('qa','directory'):
            raise ValueError('TRAINING_MODE 只支持 qa 或 directory')
        self.zone
        for value in self.times.values():
            time.fromisoformat(value)
        for value in self.shifts.values():
            time.fromisoformat(value)
        if not self.workdays or any(x not in range(7) for x in self.workdays):
            raise ValueError('工作日必须是 0（周一）至 6（周日）')
        epoch = datetime(2026, 1, 1).date()
        if any(v not in (0, 1) for v in self.offsets.values()):
            raise ValueError('日报时间偏移只能是 0（当天）或 1（次日）')
        if not (self.event_time(epoch, 'open') <= self.event_time(epoch, 'deadline') <= self.event_time(epoch, 'summary')):
            raise ValueError('日报窗口、截止和汇总必须按顺序设置，可使用次日偏移')
        if min(self.budget, self.queue_size, self.concurrency, self.timeout, self.max_file_mb, self.retention) < 1:
            raise ValueError('预算、队列、并发、超时、文件限制及保留期必须为正数')
        if not 0 <= self.retries <= 5:
            raise ValueError('AI 重试上限应在 0 至 5 之间')
        self.data = Path(self.data).resolve()
        self.backups = Path(self.backups).resolve()
        return self


def load():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    get = os.getenv
    integer = lambda name, default: int(get(name, str(default)))
    boolean = lambda name: get(name, 'false').lower() == 'true'
    c = Config(token=get('TELEGRAM_BOT_TOKEN', ''), key=get('GEMINI_API_KEY', ''), model=get('GEMINI_MODEL', ''),
               owner=integer('OWNER_USER_ID', 0), chat=integer('ALLOWED_CHAT_ID', 0), timezone=get('TEAM_TIMEZONE', 'Asia/Shanghai'))
    c.data = ROOT / get('DATA_DIR', 'data')
    c.bot_username=get('BOT_USERNAME','').lstrip('@')
    c.backups = ROOT / get('BACKUP_DIR', 'data/backups')
    c.threads = {k: ('general' if get(k.upper() + '_THREAD_ID', '0').strip().lower() == 'general' else integer(k.upper() + '_THREAD_ID', 0)) for k in c.threads}
    env_times = dict(am='AM_REMINDER', am_overdue='AM_OVERDUE', pm='PM_REMINDER', pm_overdue='PM_OVERDUE', open='REPORT_OPEN', deadline='REPORT_DEADLINE', summary='SUMMARY_TIME')
    c.times = {k: get(env_times[k], v) for k, v in c.times.items()}
    c.shifts = {k: get(k.upper(), v) for k, v in c.shifts.items()}
    c.offsets = {k: integer('REPORT_OPEN_DAY_OFFSET' if k == 'open' else 'REPORT_DEADLINE_DAY_OFFSET' if k == 'deadline' else 'SUMMARY_DAY_OFFSET', 0) for k in c.offsets}
    c.report_times_confirmed = boolean('REPORT_TIMES_CONFIRMED')
    c.workdays = tuple(int(v) for v in get('WORKDAYS', '0,1,2,3,4').split(','))
    for attr, env in dict(catchup='CATCHUP_MINUTES', repost_delay='REPOST_DELAY_SECONDS', budget='AI_DAILY_BUDGET', queue_size='AI_QUEUE_SIZE', concurrency='AI_CONCURRENCY', timeout='AI_TIMEOUT_SECONDS', retries='AI_MAX_RETRIES', output_tokens='AI_MAX_OUTPUT_TOKENS', cache_seconds='AI_CACHE_SECONDS', max_file_mb='MAX_FILE_MB', retention='BACKUP_RETENTION_DAYS').items():
        setattr(c, attr, integer(env, getattr(c, attr)))
    c.ai_enabled = boolean('AI_ENABLED')
    c.training_mode = get('TRAINING_MODE','qa').strip().lower()
    c.free_confirmed = boolean('FREE_PROJECT_CONFIRMED')
    c.report_version = integer('REPORT_VERSION', 2)
    c.click_cooldown = integer('CLICK_COOLDOWN_SECONDS', 2)
    c.identity_mode=get('IDENTITY_MODE','legacy')
    c.membership_required=boolean('MEMBERSHIP_REQUIRED')
    c.internal_chat=integer('INTERNAL_CHAT_ID',0)
    c.run_mode=get('RUN_MODE','legacy')
    c.schedule_allowed=get('SCHEDULE_ENABLED','false').lower()=='true'
    c.test_run=get('TRIAL_RUN_ID','') if c.run_mode=='trial' else ''
    if c.run_mode=='trial': c.schedule_allowed=False
    if c.identity_mode=='telegram': c.ai_enabled=False
    if c.report_version>=2:
        c.times=dict(open=get('REPORT_OPEN','23:00'),deadline=get('REPORT_DEADLINE','00:00'),summary=get('SUMMARY_TIME','00:05'))
        c.offsets=dict(open=0,deadline=1,summary=1)
        c.report_times_confirmed=True
        c.ai_enabled=False
    return c.validate()
