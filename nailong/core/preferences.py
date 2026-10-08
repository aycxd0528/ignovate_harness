"""Non-secret preferences with explicit precedence and atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def safe_config_path(path: Path, boundary: Path) -> Path:
    if os.name == "nt":
        from nailong.core.safe_files import validate_windows_path, is_link_or_reparse
        validate_windows_path(path.absolute())
        for ancestor in (*reversed(path.absolute().parents), path.absolute()):
            if is_link_or_reparse(ancestor):
                raise ValueError('配置路径不能被链接或重解析点重定向。')
    path, boundary = path.absolute(), boundary.resolve()
    try:
        parts = path.relative_to(boundary).parts
    except ValueError as error:
        raise ValueError('配置路径超出允许范围。') from error
    current = boundary
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ValueError('配置路径不能被符号链接重定向。')
    if not path.resolve().is_relative_to(boundary):
        raise ValueError('配置路径超出允许范围。')
    return path


def read_config(path: Path, boundary: Path) -> dict:
    path = safe_config_path(path,boundary)
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 1024*1024:
            raise ValueError('配置文件超过 1 MiB。')
        if os.name == "nt":
            from nailong.core.safe_files import open_regular_file
            with open_regular_file(path) as source:
                data = source.read(1024*1024+1)
            if len(data) > 1024*1024:
                raise ValueError('配置文件超过 1 MiB。')
            value = json.loads(data.decode('utf-8'))
        else:
            value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'配置文件无法读取或 JSON 无效：{path.name}') from error
    if not isinstance(value,dict):
        raise ValueError(f'配置文件必须是 JSON 对象：{path.name}')
    return value


def atomic_json(path: Path, value: dict, boundary: Path) -> None:
    path = safe_config_path(path,boundary)
    encoded = json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if os.name == "nt":
        from nailong.core.safe_files import atomic_write_bytes
        atomic_write_bytes(path, encoded.encode('utf-8'))
        return
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.nailong-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        safe_config_path(path,boundary)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def validate_new_model(name: str, model_id: str, models: dict) -> tuple[str, str]:
    if not isinstance(name, str) or not isinstance(model_id, str):
        raise ValueError('模型名称和模型 ID 必须是文本。')
    name, model_id = name.strip(), model_id.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError('模型名称需为 1 到 80 个字符，不能包含换行或控制字符。')
    if name == 'default':
        raise ValueError('default 是环境默认模型的保留名称，请使用其他名称。')
    if name in models:
        raise ValueError(f'模型名称 {name} 已配置，请使用其他名称或直接切换该模型。')
    if not model_id or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in model_id):
        raise ValueError('模型 ID 不能为空或包含空白、控制字符。')
    return name, model_id


class PreferenceStore:
    def __init__(self,project_root,*,user_path=None):
        self.project_root=Path(project_root).resolve()
        original=Path(user_path).expanduser().absolute() if user_path else Path.home()/'.nailong/preferences.json'
        anchor=original.parent.parent
        self.user_boundary=anchor.resolve()
        self.user_path=self.user_boundary/original.relative_to(anchor)
        self.local_path=self.project_root/'.nailong/settings.local.json'
        self._overrides={}
        self._cli={}
        self.default_model='deepseek-flash'
        self.default_reasoning='default'

    def effective(self,default_model=None,cli=None,*,_replacement=None) -> dict:
        self.default_model=default_model or self.default_model
        if cli is not None: self._cli=dict(cli)
        result={'theme':'dark','output_style':'normal','model':'default','disabled_skills':[],
                'reasoning_effort':self.default_reasoning,
                'models':{'default':{'model':self.default_model}}}
        sources={key:'default' for key in result}
        for path,boundary in ((self.user_path,self.user_boundary),
                              (self.project_root/'.nailong/settings.json',self.project_root),
                              (self.local_path,self.project_root)):
            data=_replacement[1] if _replacement is not None and path == _replacement[0] else read_config(path,boundary)
            for key in result:
                if key not in data: continue
                if key=='models':
                    models=data[key]
                    if not isinstance(models,dict): raise ValueError('models 必须是名称到模型声明的映射。')
                    for name,definition in models.items():
                        if (not isinstance(name,str) or not name or len(name)>80
                                or not isinstance(definition,dict) or set(definition)!={'model'}
                                or not isinstance(definition['model'],str) or not definition['model'].strip()):
                            raise ValueError('模型声明只能包含非空 model，不允许 API 地址或密钥字段。')
                    result['models'].update(models)
                else:
                    result[key]=data[key]
                sources[key]=str(path)
        for values,source in ((self._cli,'cli'),(self._overrides,'session')):
            for key,value in values.items():
                if key in {'model','theme','output_style','reasoning_effort'} and value is not None:
                    result[key]=value
                    sources[key]=source
        result['models']['default']={'model':self.default_model}
        if result['theme'] not in {'dark','light','ansi'}: raise ValueError('theme 只能为 dark、light 或 ansi。')
        if result['output_style'] not in {'concise','normal','detailed'}: raise ValueError('output_style 无效。')
        disabled=result['disabled_skills']
        if not isinstance(disabled,list) or any(not isinstance(name,str) for name in disabled):
            raise ValueError('disabled_skills 必须是名称列表。')
        selection=result['model']
        if not isinstance(selection,str): raise ValueError('model 必须是模型名称。')
        definitions=result['models']
        if selection in definitions:
            result['model_name']=selection
            result['model']=definitions[selection]['model']
        elif selection in {item['model'] for item in definitions.values()}:
            result['model_name']=selection
        else:
            raise ValueError('模型未配置；用 /model 查看可选模型。')
        from nailong.core.reasoning import model_reasoning_kwargs
        model_reasoning_kwargs(result['model'], result['reasoning_effort'])
        result['sources']=sources
        return result

    def set(self,key,value,*,global_scope=False) -> dict:
        if key not in {'model','theme','output_style','reasoning_effort'}: raise ValueError('只允许设置 model、theme、output_style、reasoning_effort。')
        previous=dict(self._overrides)
        self._overrides[key]=value
        try:
            result=self.effective()
            path,boundary=(self.user_path,self.user_boundary) if global_scope else (self.local_path,self.project_root)
            data=read_config(path,boundary)
            data[key]=value
            atomic_json(path,data,boundary)
        except BaseException:
            self._overrides=previous
            raise
        return result

    def add_model(self,name,model_id,*,global_scope=False) -> dict:
        """Save a non-secret model declaration and select it in one atomic write."""
        name,model_id=validate_new_model(name,model_id,self.effective()['models'])
        path,boundary=(self.user_path,self.user_boundary) if global_scope else (self.local_path,self.project_root)
        data=read_config(path,boundary)
        data['models']={**data.get('models',{}),name:{'model':model_id}}
        data['model']=name
        previous=dict(self._overrides)
        self._overrides['model']=name
        try:
            result=self.effective(_replacement=(path,data))
            atomic_json(path,data,boundary)
        except BaseException:
            self._overrides=previous
            raise
        return result

    def set_skill_enabled(self,name,enabled) -> None:
        data=read_config(self.local_path,self.project_root)
        disabled=set(self.effective()['disabled_skills'])
        disabled.discard(name) if enabled else disabled.add(name)
        data['disabled_skills']=sorted(disabled)
        atomic_json(self.local_path,data,self.project_root)
