"""
NetPulse py2app 配置 - 打包成 .app 双击启动
"""
from setuptools import setup
import os

APP = ['src/app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,           # macOS 不需要 X11 argv 模拟
    'plist': {
        'CFBundleName': 'NetPulse',
        'CFBundleDisplayName': 'NetPulse',
        'CFBundleIdentifier': 'com.local.NetPulse',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSUIElement': True,           # 不在 Dock 显示,只挂菜单栏
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription': 'NetPulse 用于监控网络状态',
    },
    'packages': ['rumps'],
    'includes': ['diagnostics'],
    'excludes': ['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    'iconfile': None,
}

setup(
    app=APP,
    name='NetPulse',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
