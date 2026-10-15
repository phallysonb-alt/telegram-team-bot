import re,os
def private(c,param):
 name=getattr(c,'bot_username','') or os.getenv('BOT_USERNAME','').lstrip('@')
 if not name:
  match=re.match(r'https://t\.me/([A-Za-z0-9_]+)',os.getenv('PROGRESS_MINIAPP_LINK',''));name=match[1] if match else ''
 if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}',name):raise ValueError('尚未取得机器人用户名，请检查本公司配置')
 return f'https://t.me/{name}?start={param}'
