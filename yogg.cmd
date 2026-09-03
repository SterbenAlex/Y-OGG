@echo off
chcp 65001 >nul
py -3 "%~dp0y_ogg.py" %*
