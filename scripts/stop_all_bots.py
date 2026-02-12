#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для остановки всех запущенных экземпляров бота
"""
import sys
import os
import io

# Установка правильной кодировки для вывода в Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    import psutil
except ImportError:
    print("Ошибка: psutil не установлен")
    print("Установите: pip install psutil")
    sys.exit(1)


def stop_all_bots():
    """Останавливает все процессы run_local.py"""
    current_pid = os.getpid()
    stopped = []
    
    print("Поиск запущенных экземпляров бота...")
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue
            
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                
                if 'run_local.py' in cmdline_str:
                    print(f"   Найден: PID {proc.info['pid']}")
                    try:
                        proc.kill()
                        stopped.append(proc.info['pid'])
                        print(f"   OK Остановлен: PID {proc.info['pid']}")
                    except Exception as e:
                        print(f"   ОШИБКА: Не удалось остановить PID {proc.info['pid']}: {e}")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    if stopped:
        print(f"\nОстановлено процессов: {len(stopped)}")
        print(f"PIDs: {', '.join(map(str, stopped))}")
    else:
        print("\nЗапущенных экземпляров бота не найдено")
    
    return len(stopped)


if __name__ == "__main__":
    print("=" * 60)
    print("  Остановка всех экземпляров бота")
    print("=" * 60)
    
    count = stop_all_bots()
    
    print("=" * 60)
    
    sys.exit(0 if count >= 0 else 1)
