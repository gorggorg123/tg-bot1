#!/usr/bin/env python3
"""Проверка, не запущен ли уже бот."""
import sys
import psutil

def check_bot_running():
    """Проверяет, запущен ли бот (процесс python с run_local.py или main.py)."""
    current_pid = psutil.Process().pid
    bot_processes = []
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                
                # Пропускаем текущий процесс
                if proc.info['pid'] == current_pid:
                    continue
                
                # Ищем run_local.py или main.py
                if 'run_local.py' in cmdline_str or 'uvicorn main:app' in cmdline_str:
                    bot_processes.append({
                        'pid': proc.info['pid'],
                        'cmdline': cmdline_str
                    })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    return bot_processes

if __name__ == '__main__':
    running = check_bot_running()
    
    if running:
        print("WARNING: Bot instances are already running:")
        for proc in running:
            print(f"  PID {proc['pid']}: {proc['cmdline']}")
        print("\nStop them before starting a new instance:")
        print("  Windows: taskkill /F /PID <PID>")
        print("  Linux: kill <PID>")
        sys.exit(1)
    else:
        print("OK: No bot instances running, safe to start.")
        sys.exit(0)
