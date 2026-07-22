#!/usr/bin/env python3
"""
羊羊股市监测 - 函数体内 import 完整性扫描器

功能:
  - 扫描指定 Python 文件, 检查每个 def 函数体内使用的别名是否在
    (1) 函数体内 import 或 (2) 文件顶层 import 中可用
  - 排除 self-file 自身
  - 排除字符串字面量 + 注释行
  - 排除嵌套 def 内部的 import (Python 嵌套作用域规则)

用法:
  ./venv/bin/python3 scripts/check_function_body_imports.py [path1] [path2] ...
  ./venv/bin/python3 scripts/check_function_body_imports.py          # 默认扫 fetch_data.py + tabs/ + lib/

设计目标:
  - 0 误报
  - 0 漏报 (排除 self-file / 字符串 / 注释 / 嵌套 def 后)
  - 一行一告警, 直接列出 函数名 + 行号 + 缺失别名

2026-07-22 老大要"完美"全栈排查, 此脚本作为重构期必跑验证工具沉淀.
"""
from __future__ import annotations
import ast
import os
import sys
from pathlib import Path
from typing import Iterable


def collect_top_level_aliases(tree: ast.Module) -> set[str]:
    """收集文件顶层 (不在 def/class 内) 的所有 import 别名"""
    aliases = set()

    def visit(node, in_top_level=True):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not in_top_level:
                return
            # 进入 def/class 后, 递归其子节点但不在顶层标记
            for child in node.body:
                visit(child, in_top_level=False)
            return
        if isinstance(node, ast.ImportFrom):
            for n in node.names:
                aliases.add(n.asname or n.name)
            return
        if isinstance(node, ast.Import):
            for n in node.names:
                aliases.add(n.asname or n.name.split('.')[0])
            return
        if isinstance(node, ast.If):
            # TYPE_CHECKING 块: 不算 top-level import (但通常应保留)
            # 简化处理: TYPE_CHECKING 块内的 import 也算 (有 if 包裹)
            for child in node.body:
                visit(child, in_top_level=False)
            return
        # 其他节点 (赋值/表达式等): 跳过
        for child in ast.iter_child_nodes(node):
            visit(child, in_top_level=in_top_level)

    visit(tree, in_top_level=True)
    return aliases


def collect_function_imports(func_node: ast.FunctionDef) -> set[str]:
    """收集函数体内 (不含嵌套 def 内部) 的所有 import 别名"""
    aliases = set()
    # 函数体顶层的 import (不包括嵌套 def 内部)
    for stmt in func_node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # 嵌套 def/class 内的 import 不算
            continue
        if isinstance(stmt, ast.ImportFrom):
            for n in stmt.names:
                aliases.add(n.asname or n.name)
        elif isinstance(stmt, ast.Import):
            for n in stmt.names:
                aliases.add(n.asname or n.name.split('.')[0])
    return aliases


def function_uses_name(func_node: ast.FunctionDef, alias: str) -> bool:
    """检查函数体内是否使用 alias.xxx 形式 (排除字符串/注释, 排除嵌套 def)"""
    target = alias + '.'

    def visit(node, in_nested_def=False):
        # 嵌套 def/class 内停止 (Python 作用域规则)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and in_nested_def:
            return False
        # 字符串字面量: 跳过
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return False
        # 检查 Attribute 节点 (x.y 形式)
        if isinstance(node, ast.Attribute) and node.value:
            if isinstance(node.value, ast.Name) and node.value.id == alias:
                return True
        # 递归子节点
        new_in_nested = in_nested_def or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for child in ast.iter_child_nodes(node):
            if visit(child, in_nested_def=new_in_nested):
                return True
        return False

    # 跳过函数 docstring
    body_to_check = func_node.body
    if body_to_check and isinstance(body_to_check[0], ast.Expr):
        if isinstance(body_to_check[0].value, ast.Constant) and isinstance(body_to_check[0].value.value, str):
            body_to_check = body_to_check[1:]

    for stmt in body_to_check:
        if visit(stmt):
            return True
    return False


def check_file(filepath: str, common_aliases: set[str] = None) -> list[str]:
    """
    检查单个文件的函数体 import 完整性.
    
    Args:
        filepath: 文件路径
        common_aliases: 文件级 (从调用方聚合) 的别名集合, 用于跨文件传递
    
    Returns:
        告警列表, 每行格式: 'L{line} {funcname}: uses {alias} but no import'
    """
    if common_aliases is None:
        common_aliases = set()

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            src = f.read()
    except Exception as e:
        return [f'[ERR] cannot read {filepath}: {e}']

    try:
        tree = ast.parse(src, filename=filepath)
    except SyntaxError as e:
        return [f'[ERR] syntax error in {filepath}: {e}']

    # 文件级 import 别名
    file_aliases = collect_top_level_aliases(tree)
    # self-file: 模块名本身是隐式可用的
    module_name = filepath.replace('/', '.').replace('.py', '')
    file_aliases.add(module_name)
    # 合并调用方传入的 aliases
    file_aliases.update(common_aliases)

    # 待检测的别名候选: 从整个代码库反推
    # (从所有 .py 文件的 import 收集 + 业务中常用的别名)
    candidates = _collect_all_aliases_across_codebase(filepath)

    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # 跳过嵌套 def (它们有独立作用域)
        # 这里用 walk 也会访问嵌套, 但每个 def 都会被检查, 这是 OK 的:
        # 因为嵌套 def 是独立作用域, 它们的 import 也必须独立
        func_imports = collect_function_imports(node)
        for alias in candidates:
            if alias in func_imports:
                continue
            if alias in file_aliases:
                continue
            if function_uses_name(node, alias):
                issues.append(f'  L{node.lineno} {node.name}: uses {alias}.* but no import (neither body nor file-level)')

    return issues


def _collect_all_aliases_across_codebase(current_file: str) -> set[str]:
    """从整个代码库 (fetch_data.py + tabs/ + lib/) 收集所有出现的 import 别名.
    这样 candidates 是动态的, 能抓到任何未知别名."""
    aliases = set()
    candidates_to_scan = ['fetch_data.py', 'tabs/', 'lib/']
    for target in candidates_to_scan:
        if not os.path.exists(target):
            continue
        if os.path.isfile(target):
            files = [target]
        else:
            files = []
            for root, dirs, fs in os.walk(target):
                dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', '.bak-20260717')]
                for f in fs:
                    if f.endswith('.py') and '.bak' not in f:
                        files.append(os.path.join(root, f))
        for f in files:
            try:
                with open(f) as fp:
                    src = fp.read()
                tree = ast.parse(src)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for n in node.names:
                            aliases.add(n.asname or n.name)
                    elif isinstance(node, ast.Import):
                        for n in node.names:
                            aliases.add(n.asname or n.name.split('.')[0])
            except Exception:
                pass
    return aliases


def main():
    """主入口"""
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = ['fetch_data.py', 'tabs/', 'lib/']

    files_to_check = []
    for t in targets:
        if os.path.isfile(t):
            files_to_check.append(t)
        elif os.path.isdir(t):
            for root, dirs, files in os.walk(t):
                # 排除 .bak / __pycache__
                dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', '.bak-20260717')]
                for f in files:
                    if f.endswith('.py') and '.bak' not in f:
                        files_to_check.append(os.path.join(root, f))

    if not files_to_check:
        print('No Python files to check.')
        return 1

    total_issues = 0
    for filepath in sorted(set(files_to_check)):
        issues = check_file(filepath)
        if issues:
            print(f'[FAIL] {filepath}:')
            for i in issues:
                print(i)
            total_issues += len(issues)

    print()
    if total_issues == 0:
        print(f'✅ All {len(set(files_to_check))} files: 0 import gaps')
        return 0
    else:
        print(f'❌ Total {total_issues} import gaps found in {len(set(files_to_check))} files')
        return 1


if __name__ == '__main__':
    sys.exit(main())