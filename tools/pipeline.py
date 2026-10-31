#!/usr/bin/env python3
"""Portable OpenCode engineering pipeline configuration and deterministic gates; stdlib only.
This is a deterministic LOCAL gate, not proof of actual LLM session identity.
"""
import argparse
import fnmatch
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import shlex

REPO = Path(__file__).resolve().parents[1]
ROLES = ('pipeline-orchestrator','pipeline-analyst','pipeline-architect-l1','pipeline-architect-l2','pipeline-architect-l3','pipeline-design-a','pipeline-design-b','pipeline-implementer','pipeline-code-a','pipeline-code-b')
ARCHS = ('pipeline-architect-l1','pipeline-architect-l2','pipeline-architect-l3')
REVIEWERS = ('pipeline-design-a','pipeline-design-b','pipeline-code-a','pipeline-code-b')
FAMILY_MAP = {'openai':'openai','anthropic':'anthropic','minimax':'minimax','moonshotai':'kimi','kimi':'kimi','z-ai':'glm','zai':'glm','glm':'glm','qwen':'qwen','deepseek':'deepseek','google':'google','gemini':'google'}
NAME_PATTERN = re.compile(r'^[a-z][a-z0-9-]{1,60}$')
MODEL_PATTERN = re.compile(r'^[a-z0-9_-]+/[A-Za-z0-9_./:-]+(?:#[A-Za-z0-9_-]+)?$')
EVIDENCE_PATTERN = re.compile(r'^.+:\d+\s+\|\s+\S.*$')
WORKFLOW_VERSION = 2

class PipelineError(Exception): pass

def die(message): raise PipelineError(message)

def read_json(p, default=None):
    if not p.exists():
        if default is None: die('missing file: '+str(p))
        return default
    return json.loads(p.read_text(encoding='utf8'))

def atomic_text(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp=p.with_name(p.name+'.pipetmp')
    tmp.write_text(text,encoding='utf8')
    tmp.replace(p)

def atomic_json(p, data): atomic_text(p,json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+'\n')

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()

def json_hash(value):
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
    return hashlib.sha256(encoded).hexdigest()

def model_in_file(p):
    t=p.read_text(encoding='utf8')
    m=re.search(r'(?m)^model: (\S+)\s*$',t)
    if not m: die(f'agent has no explicit model: {p}')
    return m.group(1)

def replace_model(t, model):
    changed,n=re.subn(r'(?m)^model: \S+\s*$', 'model: '+model,t,count=1)
    if n!=1: die('agent missing exactly one model line')
    return changed

def family(model):
    selector=model.split('#',1)[0]
    if not MODEL_PATTERN.fullmatch(model): die('bad model selector '+model)
    parts=selector.lower().split('/')
    if parts[0]=='openrouter':
        if len(parts)<3: die('OpenRouter model must include maker: '+model)
        maker=parts[1]
    else: maker=parts[0]
    return FAMILY_MAP.get(maker) or die('unrecognized model family; update the family classifier with independent evidence: '+model)

def active_models(): return {role:model_in_file(REPO/'agents'/f'{role}.md') for role in ROLES}

def independent(models):
    for group in ((('pipeline-architect-l1','pipeline-design-a','pipeline-design-b')),
                  (('pipeline-architect-l2','pipeline-design-a','pipeline-design-b')),
                  (('pipeline-architect-l3','pipeline-design-a','pipeline-design-b')),
                  (('pipeline-implementer','pipeline-code-a','pipeline-code-b'))):
        fams=[family(models[r]) for r in group]
        if len(set(fams))!=3: die(f'family collision: {dict(zip(group,fams))}. Change models for this route before continuing')
    if set(models)!=set(ROLES): die('model assignment does not cover 10 roles')
    return True

def print_routes(models):
    for k in ROLES: print(f'  {k:<22} {models[k]:<54} [{family(models[k])}]')

def cmd_profile(a):
    if a.op=='list':
        for p in sorted((REPO/'profiles').glob('*.json')): print(p.stem)
        return
    if a.op=='show':
        path=REPO/'profiles'/f'{a.name}.json'; models=read_json(path)
        independent(models); print_routes(models);return
    p=REPO/'profiles'/f'{a.name}.json'
    models=read_json(p);independent(models)
    for role,model in models.items(): model_in_file(REPO/'agents'/f'{role}.md')
    if a.op=='use':
        for role,model in models.items():
            path=REPO/'agents'/f'{role}.md';atomic_text(path,replace_model(path.read_text(encoding='utf8'),model))
        atomic_text(REPO/'.active-profile',a.name+'\n')
        print('Applied profile:',a.name)
        print('Manual per-role changes can be made with `model set`; next profile use overwrites them.')
    print_routes(models)
    maybe_reload()

def cmd_model(a):
    if a.op=='list': independent(active_models());print_routes(active_models());return
    if a.role not in ROLES: die('unknown role: '+str(a.role))
    current=active_models()
    current[a.role]=a.selector
    try:
        independent(current)
    except PipelineError as exc:
        print('ROUTING WARNING: '+str(exc)+'; model change saved, but reviews/finish remain blocked until repaired',file=sys.stderr)
    p=REPO/'agents'/f'{a.role}.md'
    atomic_text(p,replace_model(p.read_text(encoding='utf8'),a.selector))
    print('Changed:',a.role,'->',a.selector)
    maybe_reload()

def maybe_reload():
    exe=shutil.which('opencode')
    if exe:
        result=subprocess.run([exe,'reload'],capture_output=True,text=True,timeout=15)
        print('OpenCode reload:', result.stdout.strip() if result.returncode==0 else ('not available; reload manually: '+result.stderr.strip()))
    else: print('OpenCode not on PATH; run `opencode reload` in its CLI environment.')

def backup_move(src,dst):
    if src.exists() or src.is_symlink():
        dst.parent.mkdir(parents=True,exist_ok=True)
        # Move link itself. Never dereference and never delete its target.
        shutil.move(str(src),str(dst))

def agent_parts(role):
    text=(REPO/'agents'/f'{role}.md').read_text(encoding='utf8')
    parts=text.split('---',2)
    if len(parts)<3:die('invalid agent frontmatter: '+role)
    description=next((x.split(':',1)[1].strip() for x in parts[1].splitlines() if x.startswith('description:')),role)
    return description,parts[2].strip()

def setup_cli_wrapper(backup=None):
    home=Path.home()
    shared=home/'.local/share/engineering-pipeline'
    shared.parent.mkdir(parents=True,exist_ok=True)
    if shared.exists() or shared.is_symlink():
        if shared.is_symlink() and shared.resolve()==REPO.resolve():pass
        else:
            if backup is None:die('existing shared engineering-pipeline path; use a safe backup first')
            backup_move(shared,backup/'shared-pipeline')
    if not (shared.exists() or shared.is_symlink()):
        shared.symlink_to(REPO,target_is_directory=True)
    bindir=home/'.local/bin';bindir.mkdir(parents=True,exist_ok=True)
    wrapper=bindir/'engpipe'
    script='#!/usr/bin/env sh\nexec python3 "$HOME/.local/share/engineering-pipeline/tools/pipeline.py" "$@"\n'
    if wrapper.exists() or wrapper.is_symlink():
        if wrapper.is_file() and wrapper.read_text(encoding='utf8')==script:return wrapper
        if backup is None:die('existing engpipe wrapper; do not overwrite without backup')
        backup_move(wrapper,backup/'engpipe')
    atomic_text(wrapper,script)
    wrapper.chmod(0o755)
    return wrapper

def install_adapter(target):
    """Export role prompts for other runtimes; does NOT attest cross-family reviews."""
    if target=='claude':
        directory=Path.home()/'.claude/agents'
    elif target=='codex':
        directory=Path.home()/'.codex/agents'
    else:die('unknown adapter')
    directory.mkdir(parents=True,exist_ok=True)
    backup=directory/('.engineering-backup-'+dt.datetime.now().strftime('%Y%m%d%H%M%S'))
    for role in ROLES:
        description,body=agent_parts(role)
        # Tool names, model IDs and permission defaults differ between products.
        # The portable task/gate CLI remains the authority across hosts.
        body=body.replace('~/.config/opencode/engineering-pipeline/tools/pipeline.py','engpipe')
        body+='\n\nThe portable CLI `engpipe` owns task state and checks. Missing session metadata must never block execution. Do not assert cross-family independent review without verified external model access.'
        if target=='claude':
            output='---\nname: '+role+'\ndescription: '+description+'\n---\n\n'+body+'\n'
            filename=role+'.md'
        else:
            output=('name = '+json.dumps(role)+'\n'
                    'description = '+json.dumps(description)+'\n'
                    'developer_instructions = '+json.dumps(body,ensure_ascii=False)+'\n')
            filename=role+'.toml'
        dest=directory/filename
        if dest.exists():
            backup.mkdir(exist_ok=True)
            shutil.copy2(dest,backup/filename)
        atomic_text(dest,output)
    print('Exported role templates to',directory)
    print('Portable CLI:',setup_cli_wrapper(directory/('.engineering-backup-'+dt.datetime.now().strftime('%Y%m%d%H%M%S'))))
    print('MODEL WARNING: host-native provider/model mapping is not interchangeable. Configure external independent review separately; portability means prompts + core gates, not identical subagent APIs.')
    print('Install the CLI wrapper via install.sh or run: python3 /path/to/repo/tools/pipeline.py')

def cmd_install(a):
    if a.target!='opencode':
        return install_adapter(a.target)
    independent(active_models())
    home=Path.home(); config=home/'.config/opencode'; global_agents=config/'agents'
    global_agents.mkdir(parents=True,exist_ok=True)
    backup=config/'pipeline-backups'/dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    # install safely; keep prior auth outside config unchanged
    conf=config/'opencode.json'
    existing=read_json(conf,default={})
    if not isinstance(existing,dict): die('global opencode.json is not a JSON object')
    # backup first, don't alter credentials
    if conf.exists(): shutil.copy2(conf,backup/'opencode.json')
    for p in global_agents.glob('pipeline-*.md'): backup_move(p,backup/'agents'/p.name)
    for role in ROLES:
        (global_agents/f'{role}.md').symlink_to(REPO/'agents'/f'{role}.md')
    backup_move(config/'AGENTS.md',backup/'AGENTS.md')
    (config/'AGENTS.md').symlink_to(REPO/'AGENTS.md')
    backup_move(config/'engineering-pipeline',backup/'engineering-pipeline')
    (config/'engineering-pipeline').symlink_to(REPO,target_is_directory=True)
    print('Portable CLI:',setup_cli_wrapper(backup),'(add ~/.local/bin to PATH)')
    existing['$schema']='https://opencode.ai/config.json'
    existing['default_agent']='pipeline-orchestrator'
    # deliberately no overriding root GPT model, provider settings or auth
    atomic_json(conf,existing)
    print('Installed 10 agents globally. Backup:',backup)
    print('Provider credentials and external API keys were not modified.')
    maybe_reload()

def cmd_doctor(a):
    print('Repository:',REPO)
    models=active_models();independent(models)
    print('Model family separation: PASS (static config only)')
    print_routes(models)
    config=Path.home()/'.config/opencode'
    print('Global agent symlinks:',sum((config/'agents'/f'{x}.md').is_symlink() for x in ROLES),'/10')
    exe=shutil.which('opencode')
    if not exe:
        print('OpenCode CLI absent; cannot check catalog or active config');return
    r=subprocess.run([exe,'debug','agents'],capture_output=True,text=True,timeout=30)
    if r.returncode:
        print('OpenCode debug agents unavailable:',r.stderr[:300]);return
    agents={x['id']:x for x in json.loads(r.stdout)}
    bad=[]
    for role in ROLES:
        o=agents.get(role)
        if not o:bad.append(role+' not loaded');continue
        got=o.get('model') or {};observed=(got.get('providerID','')+'/'+got.get('id','')).strip('/')
        if observed!=models[role].split('#')[0]:bad.append(f'{role} model mismatch: {observed}')
    if bad: die('OpenCode runtime config differs: '+'; '.join(bad))
    print('OpenCode loaded model IDs: PASS (not a live API entitlement test)')
    cat=subprocess.run([exe,'models'],capture_output=True,text=True,timeout=30)
    if cat.returncode==0:
        listed=set(cat.stdout.splitlines());absent=[m for m in set(models.values()) if m.split('#')[0] not in listed]
        if absent: print('Not in local catalog (check `/connect` and `opencode models`):',absent)
        else:print('All active model IDs in catalog (not proof of API access).')

def project_root():
    p=subprocess.run(['git','rev-parse','--show-toplevel'],capture_output=True,text=True)
    if p.returncode:die('run inside a Git repository')
    return Path(p.stdout.strip()).resolve()

def tdir(root,slug):
    if not NAME_PATTERN.fullmatch(slug):die('invalid slug: use lowercase Latin, numbers and hyphens')
    return root/'.engineering-pipeline'/'tasks'/slug

def task_json(root,slug):return tdir(root,slug)/'state.json'

def get_state(root,slug):return read_json(task_json(root,slug))

def save_state(root,slug,data):atomic_json(task_json(root,slug),data)

def file_hash(p):
    if p.is_symlink(): return hashlib.sha256(os.readlink(p).encode()).hexdigest()
    if not p.is_file():return 'MISSING'
    h=hashlib.sha256()
    with p.open('rb') as fh:
        while chunk:=fh.read(1048576):h.update(chunk)
    return h.hexdigest()

def file_snapshot(root):
    """Tracked and non-ignored untracked files; task state is excluded."""
    def git(args):
        out=subprocess.run(["git"]+args,cwd=root,capture_output=True)
        if out.returncode:die("git snapshot failed: "+out.stderr.decode(errors="replace"))
        return out.stdout
    found=git(["ls-files","-z"])+git(["ls-files","--others","--exclude-standard","-z"])
    result={}
    for val in found.split(b"\0"):
        if not val:continue
        rel=os.fsdecode(val)
        if rel.startswith(".engineering-pipeline/"):continue
        parts=Path(rel).parts
        if any(x in parts for x in ("__pycache__",".pytest_cache",".mypy_cache",".ruff_cache","node_modules","coverage",".turbo")):continue
        if rel.endswith((".pyc",".tsbuildinfo",".eslintcache")):continue
        result[rel]=file_hash(root/rel)
    return result

def fingerprint(root):
    h=hashlib.sha256()
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True)
    if head.returncode:die('HEAD commit required for fingerprint; make one initial local commit')
    h.update(head.stdout.strip().encode())
    for rel,digest in sorted(file_snapshot(root).items()):
        h.update(rel.encode('utf8',errors='surrogateescape')+b'\0')
        h.update(digest.encode()+b'\0')
    return h.hexdigest()[:24]

def changed_files_since(before, after):
    return sorted(path for path in set(before)|set(after) if before.get(path)!=after.get(path))

def checks_config(root):
    cfg=read_json(root/'.engineering-pipeline'/'checks.json')
    if not isinstance(cfg,dict) or not isinstance(cfg.get('components'),dict):die('checks.json must declare components')
    return cfg

def validate_command(value, where):
    if not isinstance(value,list) or not value or not all(isinstance(x,str) and x for x in value):
        die('invalid argv command in '+where+'; expected ["program","arg",...]')
    return value

def require_preflight(s):
    """Legacy in-flight tasks remain recoverable; version 2 tasks fail closed."""
    if s.get('workflow_version',1)<WORKFLOW_VERSION:return
    pre=s.get('preflight',{})
    if pre.get('status')!='READY':die('preflight is incomplete or has blocking questions')

def require_analysis_context(root,s):
    if s.get('workflow_version',1)<WORKFLOW_VERSION:return
    require_preflight(s)
    rec=s.get('analysis_context',{})
    path=tdir(root,s['slug'])/'analysis-context.json'
    if rec.get('status')!='READY' or not path.is_file():die('evidence-based analysis context is required')
    if rec.get('artifact_hash')!=file_hash(path):die('analysis context artifact changed; record it again')
    if rec.get('preflight_fingerprint')!=s['preflight'].get('fingerprint'):die('analysis context is stale for current preflight')
    if rec.get('code_fingerprint')!=fingerprint(root):die('analysis context is stale for current pre-implementation code')

def relative_file(root,value,where):
    if not isinstance(value,str) or not value:die('invalid path in '+where)
    path=(root/value).resolve()
    if not path.is_relative_to(root):die('path outside repository in '+where+': '+value)
    if not path.is_file():die('missing declared file in '+where+': '+value)
    return path

def gate_context_fingerprint(root,cfg,commands):
    """Return a cache key only for an explicitly complete validation context.

    Probe output and selected environment values are hashed, never persisted.
    Invalid declarations fail; unavailable probes merely disable reuse so the
    authoritative full gate can still run.
    """
    cache=cfg.get('gate_cache')
    if not isinstance(cache,dict) or not cache.get('enabled'):return None
    dependency_files=cache.get('dependency_files')
    config_files=cache.get('config_files')
    probes=cache.get('environment')
    env_vars=cache.get('env_vars',[])
    if not isinstance(dependency_files,list) or not dependency_files:
        print('GATE CACHE DISABLED: gate_cache.dependency_files must be non-empty',file=sys.stderr);return None
    if not isinstance(config_files,list) or not config_files:
        print('GATE CACHE DISABLED: gate_cache.config_files must be non-empty',file=sys.stderr);return None
    if not isinstance(probes,list) or not probes:
        print('GATE CACHE DISABLED: gate_cache.environment must contain identity probes',file=sys.stderr);return None
    if not isinstance(env_vars,list) or not all(isinstance(x,str) and x for x in env_vars):
        die('gate_cache.env_vars must be a list of environment variable names')
    declared={}
    for group,values in (('dependency_files',dependency_files),('config_files',config_files)):
        if not all(isinstance(x,str) and x for x in values):die('gate_cache.'+group+' must contain paths')
        declared[group]={value:file_hash(relative_file(root,value,'gate_cache.'+group)) for value in sorted(set(values))}
    observed=[]
    for index,value in enumerate(probes):
        argv=validate_command(value,'gate_cache.environment['+str(index)+']')
        executable=shutil.which(argv[0])
        if not executable:
            print('GATE CACHE DISABLED: environment probe executable unavailable: '+argv[0],file=sys.stderr);return None
        try:
            result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=30)
        except (subprocess.TimeoutExpired,OSError) as exc:
            print('GATE CACHE DISABLED: environment probe failed: '+str(exc),file=sys.stderr);return None
        if result.returncode:
            print('GATE CACHE DISABLED: environment probe returned '+str(result.returncode)+': '+shlex.join(argv),file=sys.stderr);return None
        resolved=Path(executable).resolve()
        observed.append({'argv':argv,'executable':str(resolved),'executable_hash':file_hash(resolved),
                         'output_hash':hashlib.sha256((result.stdout+'\0'+result.stderr).encode('utf8')).hexdigest()})
    payload={'code_fingerprint':fingerprint(root),'checks_hash':file_hash(root/'.engineering-pipeline'/'checks.json'),
             'full_checks':[list(x) for x in commands],'declared_files':declared,
             'runtime':{'os_name':os.name,'sys_platform':sys.platform,'machine':platform.machine(),
                        'python':platform.python_version(),'implementation':platform.python_implementation()},
             'environment_probes':observed,
             'environment_values':{name:hashlib.sha256(os.environ.get(name,'<UNSET>').encode('utf8')).hexdigest()
                                   for name in sorted(set(env_vars))}}
    return json_hash(payload)

def impact_checks(cfg, changed):
    components=cfg['components']
    direct=set()
    unknown=[]
    for path in changed:
        matches={name for name,entry in components.items()
                 if isinstance(entry,dict) and any(fnmatch.fnmatchcase(path,pattern) for pattern in entry.get('paths',[]))}
        if not matches:unknown.append(path)
        direct.update(matches)
    if unknown:die('unmapped changed paths; update .engineering-pipeline/checks.json before slice check: '+', '.join(unknown[:20]))
    # "depends_on" means consumer -> dependencies. If a dependency changes,
    # we also check every transitive consumer downstream.
    impacted=set(direct)
    while True:
        newly={name for name,entry in components.items()
               if isinstance(entry,dict) and set(entry.get('depends_on',[])) & impacted}
        if newly <= impacted:break
        impacted |= newly
    commands=[]
    for name in sorted(impacted):
        entry=components[name]
        if not isinstance(entry.get('slice_checks'),list) or not entry['slice_checks']:
            die('component '+name+' has no slice_checks; cannot silently skip coverage')
        for command in entry['slice_checks']:
            argv=tuple(validate_command(command,name))
            if argv not in commands:commands.append(argv)
    return sorted(impacted),commands

def run_checks(root,commands,logfile):
    blocks=[];ok=True
    for cmd in commands:
        try:
            out=subprocess.run(list(cmd),cwd=root,capture_output=True,text=True,timeout=1800)
            blocks.append('$ '+shlex.join(cmd)+'\n'+out.stdout+'\n'+out.stderr+'\nexit='+str(out.returncode))
            if out.returncode:ok=False;break
        except (subprocess.TimeoutExpired,FileNotFoundError) as exc:
            blocks.append('$ '+shlex.join(cmd)+'\nERROR: '+str(exc));ok=False;break
    atomic_text(logfile,'\n\n'.join(blocks)[-100000:])
    return ok

def require_frozen(root,s):
    frozen=s.get('frozen_tests',{})
    if not frozen:die('no frozen acceptance tests; task freeze-tests is mandatory')
    for path,h in frozen.items():
        if file_hash(root/path)!=h:die('frozen acceptance test changed: '+path)

def require_design(root,s):
    if s['size']!='large':return
    d=root/'.engineering-pipeline'/'tasks'/s['slug']/'design.md'
    if not d.exists() or not d.read_text().strip():die('large task requires non-empty design.md')
    dfp=file_hash(d)
    rev=s.get('design_reviews',{})
    models=active_models()
    author=s.get('architect_role') or 'pipeline-architect-l2'
    valid=[s.get('architect_model') or models[author]]
    for letter,role in (('a','pipeline-design-a'),('b','pipeline-design-b')):
        r=rev.get(letter)
        if not r or r['fingerprint']!=dfp or r['verdict']!='PASS' or r['blockers']!=0:die('missing/failing/stale architecture review '+letter)
        if r['role']!=role:die('design review role mismatch')
        if not r.get('external_override') and r['model']!=models[role]:die('design reviewer default model changed; redo both')
        valid.append(r['model'])
    if len(set(map(family,valid)))!=3:die('design model family collision')
    if rev['a'].get('session') and rev['b'].get('session') and rev['a']['session']==rev['b']['session']:die('design reviewers shared session')
    if s.get('design_approved_fingerprint')!=dfp:die('human design approval missing/stale')

def require_gate(root,s):
    require_frozen(root,s)
    g=s.get('full_gate',{})
    if g.get('status')!='PASS' or g.get('fingerprint')!=fingerprint(root):die('full gate not green for current code fingerprint')
    if g.get('cacheable'):
        cfg=checks_config(root);raw=cfg.get('full_checks')
        if not isinstance(raw,list) or not raw:die('full gate configuration is no longer valid')
        commands=[tuple(validate_command(c,'full_checks')) for c in raw]
        current=gate_context_fingerprint(root,cfg,commands)
        if current!=g.get('context_fingerprint'):die('full gate validation context changed; run task gate again')
        log=Path(g.get('log',''))
        if g.get('log_hash')!=file_hash(log):die('full gate evidence log changed; run task gate again')

def cmd_init(a):
    root=project_root();dirname=root/'.engineering-pipeline';dirname.mkdir(exist_ok=True)
    checks=dirname/'checks.json'
    if not checks.exists():
        atomic_json(checks,{'components':{},'full_checks':[],
                            'gate_cache':{'enabled':False,'dependency_files':[],
                                          'config_files':[],'environment':[],'env_vars':[]}})
    exclusion=root/'.git/info/exclude'
    if exclusion.exists():
        val=exclusion.read_text()
        if '.engineering-pipeline/' not in val: exclusion.write_text(val.rstrip()+'\n.engineering-pipeline/\n')
    print('Project initialized:',dirname)
    print('Configure .engineering-pipeline/checks.json; empty checks deliberately fail.')

def cmd_task(a):
    root=project_root();path=task_json(root,a.slug)
    if a.op=='start':
        if path.exists():die('task already exists: '+a.slug)
        td=tdir(root,a.slug);td.mkdir(parents=True,exist_ok=True)
        s={'workflow_version':WORKFLOW_VERSION,'slug':a.slug,'kind':a.kind,'size':a.size,'created_at':now(),'architect_role':a.architect if a.size=='large' else None,'implementer_role':'pipeline-implementer','architect_model':(getattr(a,'architect_model',None) or active_models()[a.architect]) if a.size=='large' else None,'implementer_model':getattr(a,'implementer_model',None) or active_models()['pipeline-implementer'],'status':'PREFLIGHT','preflight':{},'analysis_context':{},'design_reviews':{},'code_reviews':{},'frozen_tests':{},'full_gate':{},'slice_checks':[],'slice_snapshot':file_snapshot(root)}
        save_state(root,a.slug,s);print('Task created:',td);return
    s=get_state(root,a.slug)
    if a.op=='status':
        print(json.dumps({'task':s,'current_code_fingerprint':fingerprint(root)},indent=2,ensure_ascii=False));return
    if s['status']=='DONE':die('task already DONE')
    if a.op=='preflight':
        if s.get('frozen_tests') or s.get('slice_checks') or s.get('full_gate'):
            die('cannot replace preflight after implementation validation has started')
        data={'goal':a.goal,'in_scope':a.in_scope,'out_of_scope':a.out_of_scope,
              'constraints':a.constraint,'acceptance':a.acceptance,'decisions':a.decision,
              'blocking_questions':a.blocking_question}
        artifact=tdir(root,a.slug)/'preflight.json';atomic_json(artifact,data)
        status='BLOCKED' if a.blocking_question else 'READY'
        s['preflight']={'status':status,'fingerprint':file_hash(artifact),'artifact':str(artifact),'recorded_at':now()}
        s['analysis_context']={};s['design_reviews']={};s['design_approved_fingerprint']=None;s['design_approved_at']=None
        s['status']='PREFLIGHT_BLOCKED' if status=='BLOCKED' else 'ANALYSIS'
        save_state(root,a.slug,s)
        print('Preflight',status+':',artifact)
        if status=='BLOCKED':print('Resolve material product questions and record preflight again before Analyst.')
        return
    if a.op=='analysis-context':
        require_preflight(s)
        if s.get('frozen_tests') or s.get('slice_checks') or s.get('full_gate'):
            die('cannot replace analysis context after implementation validation has started')
        bad=[item for item in a.evidence if not EVIDENCE_PATTERN.fullmatch(item)]
        if bad:die('analysis evidence must use `path:line | verified claim`: '+bad[0])
        data={'preflight_fingerprint':s['preflight']['fingerprint'],'evidence':a.evidence,
              'components':a.component,'tests':a.test,'risks':a.risk,
              'decisions':a.decision,'unknowns':a.unknown}
        artifact=tdir(root,a.slug)/'analysis-context.json';atomic_json(artifact,data)
        s['analysis_context']={'status':'READY','artifact':str(artifact),'artifact_hash':file_hash(artifact),
                               'preflight_fingerprint':s['preflight']['fingerprint'],
                               'code_fingerprint':fingerprint(root),'recorded_at':now()}
        s['design_reviews']={};s['design_approved_fingerprint']=None;s['design_approved_at']=None
        s['status']='DESIGN' if s['size']=='large' else 'IMPLEMENTATION_READY'
        save_state(root,a.slug,s);print('Analysis context READY:',artifact);return
    if a.op=='author-model':
        # A model change applies to future work and invalidates judgments about the author.
        fam=family(a.model)
        if a.role=='architect':
            if s['size']!='large':die('no architect assigned to small task')
            s['architect_model']=a.model
            s['design_reviews']={}
            s['design_approved_fingerprint']=None
            s['design_approved_at']=None
            print('Architect model updated; design reviews and approval must be repeated')
        else:
            s['implementer_model']=a.model
            s['code_reviews']={}
            print('Implementer model updated; both code reviews must be repeated')
        save_state(root,a.slug,s);return
    if a.op=='freeze-tests':
        require_analysis_context(root,s)
        if s['size']=='large':require_design(root,s)
        if not a.paths:die('specify acceptance test paths; freeze before implementation')
        frozen={}
        for rel in a.paths:
            p=(root/rel).resolve()
            if not p.is_relative_to(root):die('test outside repo')
            if not p.is_file():die('acceptance test missing: '+rel)
            frozen[str(p.relative_to(root))]=file_hash(p)
        s['frozen_tests']=frozen;save_state(root,a.slug,s);print('Frozen',len(frozen),'acceptance test(s)');return
    if a.op=='design-review':
        if s['size']!='large':die('design reviews only required for large tasks')
        require_analysis_context(root,s)
        dfp=file_hash(tdir(root,a.slug)/'design.md')
        if dfp=='MISSING':die('create design.md first')
        who='pipeline-design-'+a.focus;model=getattr(a,'model',None) or active_models()[who]
        rev={'role':who,'model':model,'external_override':bool(getattr(a,'model',None)),'fingerprint':dfp,'session':a.session,'verdict':a.verdict,'blockers':a.blockers,'findings':getattr(a,'findings',''),'recorded_at':now()}
        if not a.session:print('AUDIT WARNING: design review session ID unavailable; independent invocation cannot be verified via ID',file=sys.stderr)
        s['design_reviews'][a.focus]=rev;s['design_approved_fingerprint']=None
        save_state(root,a.slug,s);print('Saved design review',a.focus,dfp);return
    if a.op=='approve-design':
        # Strictly local interactive human approval; not agent-controlled input.
        if not sys.stdin.isatty():die('human approval requires interactive terminal; cannot be automated')
        saved=s.get('design_approved_fingerprint');s['design_approved_fingerprint']=file_hash(tdir(root,a.slug)/'design.md')
        try:require_design(root,s)
        except Exception: s['design_approved_fingerprint']=saved;raise
        expected='APPROVE '+a.slug
        print('You are approving an architectural design after both independent reviews.')
        answer=input('Type exactly '+expected+': ')
        if answer!=expected:die('approval not granted')
        s['design_approved_at']=now();save_state(root,a.slug,s);print('Human design approval recorded');return
    if a.op=='slice-check':
        if s['size']=='large':require_design(root,s)
        cfg=checks_config(root)
        before=s.get('slice_snapshot')
        if before is None:die('missing slice baseline; do not guess affected files. Re-establish task state with explicit review')
        current=file_snapshot(root)
        changed=changed_files_since(before,current)
        if not changed:die('no changed files since prior slice check')
        impacted,commands=impact_checks(cfg,changed)
        fp_before=fingerprint(root)
        log=tdir(root,a.slug)/('slice-'+str(len(s.get('slice_checks',[]))+1)+'.log')
        ok=run_checks(root,commands,log)
        fp_after=fingerprint(root)
        ok=ok and fp_before==fp_after
        record={'status':'PASS' if ok else 'FAIL','changed':changed,'components':impacted,
                'commands':[list(x) for x in commands],'fingerprint':fp_after,'log':str(log),'at':now()}
        s.setdefault('slice_checks',[]).append(record)
        if ok:s['slice_snapshot']=current
        save_state(root,a.slug,s)
        if not ok:die('targeted slice checks FAIL / code changed during check; see '+str(log))
        print('Slice checks PASS:', ', '.join(impacted),'files:',len(changed))
        return
    if a.op=='gate':
        if s['size']=='large':require_design(root,s)
        require_frozen(root,s)
        if s.get('slice_snapshot') != file_snapshot(root):
            die('unverified changes since last slice check; run task slice-check before final full gate')
        cfg=checks_config(root)
        commands=cfg.get('full_checks')
        if not isinstance(commands,list) or not commands:die('no configured full_checks in .engineering-pipeline/checks.json')
        commands=[tuple(validate_command(c,'full_checks')) for c in commands]
        before=fingerprint(root)
        log=tdir(root,a.slug)/'full-gate.log'
        context_before=gate_context_fingerprint(root,cfg,commands)
        previous=s.get('full_gate',{})
        if (context_before and previous.get('status')=='PASS' and previous.get('fingerprint')==before
                and previous.get('context_fingerprint')==context_before and previous.get('log_hash')==file_hash(log)):
            previous['reuse_count']=previous.get('reuse_count',0)+1
            previous['last_reused_at']=now();s['full_gate']=previous;save_state(root,a.slug,s)
            print('Full gate REUSED on',before,'(code, dependencies, config and environment unchanged)');return
        success=run_checks(root,commands,log)
        after=fingerprint(root)
        success=success and before==after
        context_after=gate_context_fingerprint(root,cfg,commands)
        if context_before or context_after:success=success and context_before==context_after
        s['full_gate']={'status':'PASS' if success else 'FAIL','fingerprint':after if success else '',
                        'context_fingerprint':context_after if success else None,
                        'cacheable':bool(success and context_after),'at':now(),'log':str(log),
                        'log_hash':file_hash(log) if success else None,'reuse_count':0}
        save_state(root,a.slug,s)
        if not success:die('full gate FAIL or code/environment modified during gate; see '+str(log))
        print('Full gate PASS on',after);return
    if a.op=='code-review':
        if s['size']=='large':require_design(root,s)
        require_gate(root,s)
        role='pipeline-code-'+a.focus;models=active_models();fp=fingerprint(root)
        rec={'role':role,'model':getattr(a,'model',None) or models[role],'external_override':bool(getattr(a,'model',None)),'implementer_model':s.get('implementer_model') or models['pipeline-implementer'],'fingerprint':fp,'session':a.session,'verdict':a.verdict,'blockers':a.blockers,'findings':getattr(a,'findings',''),'recorded_at':now()}
        if not a.session:print('AUDIT WARNING: code review session ID unavailable; independent invocation cannot be verified via ID',file=sys.stderr)
        s['code_reviews'][a.focus]=rec;save_state(root,a.slug,s)
        print('Saved code review',a.focus,fp);return
    if a.op=='finish':
        if s['size']=='large':require_design(root,s)
        require_gate(root,s)
        models=active_models();fp=fingerprint(root);revs=s.get('code_reviews',{})
        vals=[s.get('implementer_model') or models['pipeline-implementer']]
        for f in 'ab':
            role='pipeline-code-'+f;rev=revs.get(f)
            if not rev or rev['fingerprint']!=fp or rev['verdict']!='PASS' or rev['blockers']!=0:die('missing/failing/stale code review '+f)
            if not rev.get('external_override') and rev['model']!=models[role]:die('default reviewer model changed; redo both')
            if rev['implementer_model']!=(s.get('implementer_model') or models['pipeline-implementer']):die('implementer model changed; redo both')
            vals.append(rev['model'])
        if len(set(map(family,vals)))!=3:die('code reviewer family collision')
        if revs['a'].get('session') and revs['b'].get('session') and revs['a']['session']==revs['b']['session']:die('code reviewers shared session')
        s['session_audit']={'design_ids_present':s['size']!='large' or all(s.get('design_reviews',{}).get(x,{}).get('session') for x in 'ab'),'code_ids_present':all(revs.get(x,{}).get('session') for x in 'ab')}
        if not s['session_audit']['design_ids_present'] or not s['session_audit']['code_ids_present']:
            print('AUDIT WARNING: some child session IDs are unavailable. Review context isolation has NOT been runtime-verified.',file=sys.stderr)
        s['status']='DONE';s['finished_at']=now();save_state(root,a.slug,s)
        print('DONE: current full gate, frozen acceptance tests, independent A+B PASS and required design approval');
        print('NOTE: static model declarations and session strings are NOT verified live invocation identities.');return
    die('unknown task action')

def main():
    p=argparse.ArgumentParser(description='Portable engineering agents and deterministic gates')
    s=p.add_subparsers(dest='command',required=True)
    x=s.add_parser('install');x.add_argument('--target',choices=['opencode','claude','codex'],default='opencode');x.set_defaults(fn=cmd_install)
    x=s.add_parser('doctor');x.set_defaults(fn=cmd_doctor)
    x=s.add_parser('profile');xs=x.add_subparsers(dest='op',required=True)
    for op in ('list','show','use'):
        z=xs.add_parser(op)
        if op!='list':z.add_argument('name',choices=['max-quality','balanced','budget'])
        z.set_defaults(fn=cmd_profile)
    x=s.add_parser('model');xs=x.add_subparsers(dest='op',required=True)
    z=xs.add_parser('list');z.set_defaults(fn=cmd_model)
    z=xs.add_parser('set');z.add_argument('role');z.add_argument('selector');z.set_defaults(fn=cmd_model)
    x=s.add_parser('init');x.set_defaults(fn=cmd_init)
    x=s.add_parser('task');xs=x.add_subparsers(dest='op',required=True)
    for op in ('start','status','preflight','analysis-context','author-model','freeze-tests','design-review','approve-design','gate','slice-check','code-review','finish'):
        z=xs.add_parser(op);z.add_argument('slug')
        if op=='start':z.add_argument('--kind',choices=['feature','bugfix','refactor'],required=True);z.add_argument('--size',choices=['small','large'],required=True);z.add_argument('--architect',choices=ARCHS,default='pipeline-architect-l2');z.add_argument('--architect-model');z.add_argument('--implementer-model')
        if op=='preflight':
            z.add_argument('--goal',required=True);z.add_argument('--in-scope',action='append',required=True)
            z.add_argument('--out-of-scope',action='append',default=[]);z.add_argument('--constraint',action='append',default=[])
            z.add_argument('--acceptance',action='append',required=True);z.add_argument('--decision',action='append',default=[])
            z.add_argument('--blocking-question',action='append',default=[])
        if op=='analysis-context':
            z.add_argument('--evidence',action='append',required=True);z.add_argument('--component',action='append',default=[])
            z.add_argument('--test',action='append',default=[]);z.add_argument('--risk',action='append',default=[])
            z.add_argument('--decision',action='append',default=[]);z.add_argument('--unknown',action='append',default=[])
        if op=='freeze-tests':z.add_argument('paths',nargs='+')
        if op=='author-model':z.add_argument('--role',choices=['architect','implementer'],required=True);z.add_argument('--model',required=True)
        if op in ('design-review','code-review'):
            z.add_argument('focus',choices=['a','b']);z.add_argument('verdict',choices=['PASS','FIX']);z.add_argument('blockers',type=int);z.add_argument('--session',default=None,help='Optional actual child session ID; never invent one');z.add_argument('--model',help='Actual reviewer model from host logs, if different from OpenCode default');z.add_argument('--findings',default='')
        z.set_defaults(fn=cmd_task)
    a=p.parse_args()
    try:a.fn(a)
    except (PipelineError,ValueError,KeyError,FileNotFoundError) as e:
        print('PIPELINE ERROR:',str(e),file=sys.stderr);sys.exit(2)

if __name__=='__main__':main()
