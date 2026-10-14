"""Optional certbot entry point with Token redaction before logging/terminal output."""
import logging
import os
import re
import sys
import traceback
from pathlib import Path


def log_factory(secret):
    original=logging.getLogRecordFactory()
    def factory(*args,**kwargs):
        record=original(*args,**kwargs)
        record.msg=record.getMessage().replace(secret,'[REDACTED]')
        record.args=()
        if record.exc_info:
            record.exc_text=''.join(traceback.format_exception(*record.exc_info)).replace(secret,'[REDACTED]')
            record.exc_info=None
        if record.stack_info:
            record.stack_info=record.stack_info.replace(secret,'[REDACTED]')
        return record
    return factory


class RedactedOutput:
    def __init__(self,stream,secret):self.stream,self.secret=stream,secret
    def write(self,value):return self.stream.write(value.replace(self.secret,'[REDACTED]'))
    def flush(self):return self.stream.flush()
    def __getattr__(self,key):return getattr(self.stream,key)


def isolate_provider_environment(directory):
    # Do not let SDK globals substitute a broader credential from this root's home.
    os.environ['HOME']=str(directory)
    for name in list(os.environ):
        if name.startswith(('CLOUDFLARE_','CF_')):
            os.environ.pop(name,None)


def main():
    args=sys.argv[1:]
    try:
        index=args.index('--dns-cloudflare-credentials')
        path=Path(args[index+1])
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024 or path.stat().st_mode & 0o777!=0o600 or path.stat().st_uid!=os.geteuid():
            raise ValueError('unsafe credential')
        text=path.read_text().strip()
        if not re.fullmatch(r'dns_cloudflare_api_token = [A-Za-z0-9_-]{20,256}',text):raise ValueError('credential format')
        secret=text.split(' = ',1)[1]
        isolate_provider_environment(path.parent)
        logging.setLogRecordFactory(log_factory(secret))
        sys.stdout=RedactedOutput(sys.stdout,secret);sys.stderr=RedactedOutput(sys.stderr,secret)
        from certbot.main import main as certbot_main
        return certbot_main(args)
    except (Exception,KeyboardInterrupt):
        print('NodePilot DNS-01 certbot 未完成；检查系统 certbot/插件、Zone 权限和私有日志。',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
