"""Windows resource sampling and bounded allocation for independent searches."""
import ctypes
from ctypes import wintypes
import math
import os
import time

GIB = 1024**3


def available_memory():
    class Memory(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in
            ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
    mem = Memory(); mem.length = ctypes.sizeof(mem)
    return mem.available if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)) else 0


def worker_target(performance, count, cpus, external_cpu, memory_budget, color=False):
    """Leave two logical CPUs and 2 GiB physical RAM for interactive applications.

    memory_budget includes existing solver working sets, after the RAM reserve.
    Unknown memory never grants extra processes.
    """
    if count <= 60 or performance == 'eco': return 1
    spare = max(1, math.floor(cpus * (1 - external_cpu / 100) - 2))
    per_worker = (384 if color else 768) * 1024**2
    memory_workers = max(1, int(memory_budget // per_worker))
    ceiling = 6 if performance == 'fast' else max(1, cpus - 2)
    return max(1, min(spare, memory_workers, ceiling))


def state_cap(budget, workers, primary, color):
    # Dictionary backing arrays and resize peaks need more than key size alone.
    # Extra allowance is kept outside state storage for runtime and map objects.
    share = max(64 * 1024**2, budget * .75 / (workers + 1) * (2 if primary else 1))
    share = min(share, (1536 if primary else 512) * 1024**2)
    bytes_per_state = 256 if color else 160
    return max(50000, min(16000000 if color else 40000000,
                         int(max(0, share - 48*1024**2) / bytes_per_state)))


class ResourceSampler:
    def __init__(self):
        self.cpus = os.cpu_count() or 1
        self.kernel = ctypes.windll.kernel32
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.psapi = ctypes.windll.psapi
        self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        self.previous_system = self.system_times()
        self.previous_process = {}
        self.previous_time = time.monotonic()
        self.system_cpu = 0
        self.solver_cpu = 0

    @staticmethod
    def ticks(f): return (f.dwHighDateTime << 32) | f.dwLowDateTime

    def system_times(self):
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if self.kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return self.ticks(idle), self.ticks(kernel) + self.ticks(user)
        return None

    def process_times(self, p):
        fields = [wintypes.FILETIME() for _ in range(4)]
        if self.kernel.GetProcessTimes(int(p._handle), *(ctypes.byref(f) for f in fields)):
            return (self.ticks(fields[2]) + self.ticks(fields[3])) / 10000000
        return 0

    def process_memory(self, p):
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak_ws', 'working_set', 'peak_pool_paged',
                  'pool_paged', 'peak_pool_nonpaged', 'pool_nonpaged', 'pagefile', 'peak_pagefile', 'private')]
        mem = Counters(); mem.cb = ctypes.sizeof(mem)
        if self.psapi.GetProcessMemoryInfo(int(p._handle), ctypes.byref(mem), mem.cb):
            return mem.working_set, mem.private
        return 0, 0

    def priority(self, p, performance):
        self.kernel.SetPriorityClass(int(p._handle), 0x40 if performance == 'eco' else 0x4000)

    def sample(self, processes):
        now = time.monotonic(); elapsed = max(.001, now - self.previous_time)
        system = self.system_times()
        if system and self.previous_system:
            idle = system[0] - self.previous_system[0]; total = system[1] - self.previous_system[1]
            if total > 0: self.system_cpu = max(0, min(100, 100 * (1 - idle / total)))
        cpu_delta = 0; working = private = 0; current = {}
        for p in processes:
            cpu = self.process_times(p); current[p.pid] = cpu
            # A new worker's CPU time belongs to this sampling interval.
            cpu_delta += max(0, cpu - self.previous_process.get(p.pid, 0))
            ws, pv = self.process_memory(p); working += ws; private += pv
        self.solver_cpu = min(100, cpu_delta / elapsed / self.cpus * 100)
        self.previous_system = system; self.previous_process = current; self.previous_time = now
        free = available_memory()
        return {'logical_cpus': self.cpus, 'system_cpu_percent': round(self.system_cpu, 1),
                'solver_cpu_percent': round(self.solver_cpu, 1),
                'external_cpu_percent': round(max(0, self.system_cpu - self.solver_cpu), 1),
                'solver_memory_mb': round(working / 1024**2), 'solver_private_mb': round(private / 1024**2),
                'available_memory_gb': round(free / GIB, 2),
                'memory_budget': max(0, free + working - 2 * GIB)}
