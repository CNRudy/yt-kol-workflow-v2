#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wrapper: run main.py batch for German baby camera keywords.
Uses system proxy (Clash HTTP proxy at 127.0.0.1:7890) for YouTube API access.
"""
import os
import sys

# Keep system proxy (Clash at 7890) — do NOT clear HTTP_PROXY/HTTPS_PROXY
# Only suppress lark-cli proxy warning
os.environ['LARK_CLI_NO_PROXY_WARN'] = '1'

proxy = os.environ.get('HTTPS_PROXY', os.environ.get('HTTP_PROXY', ''))
print(f"[网络] 使用系统代理: {proxy}", flush=True)

# ---- Run main.py with batch args ----
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.argv = [
    'main.py', 'batch',
    '--keywords-file', 'keywords/keywords_baby_camera_de.txt',
    '--output-dir', 'output/german_baby_camera_batch',
    '--region', 'DE',
    '--lang', 'de',
    '--max-results', '100',
    '--min-views', '3000',
    '--min-engagement', '1.0',
    '--filter-mode', 'or',
    '--yes',
    '--no-feishu',
]

exec(open('main.py').read())
