"""Operation-owned finite FIFO: close drains; overflow is an explicit failure."""
from collections import deque
import threading


class ScanQueue:
    def __init__(self, capacity=32):
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError('scan_queue_capacity')
        self.capacity = capacity
        self.items = deque()
        self.condition = threading.Condition()
        self.closed = False
        self.last_number = 0
        self.events = []
        self.counts = dict(submitted=0, taken=0, completed=0, failed=0,
                           overflow=0, after_close=0, discarded=0, high_water=0)

    def put(self, item):
        number, begin, end, _ = item
        with self.condition:
            if number <= self.last_number or not 0 <= begin <= end:
                raise ValueError('scan_frame_order_or_time')
            self.last_number = number
            if self.closed:
                self.counts['after_close'] += 1
                self.events.append(dict(frame_number=number, read_begin=begin, read_end=end, reason='AFTER_SCAN_CLOSE'))
                return False
            if len(self.items) >= self.capacity:
                self.counts['overflow'] += 1
                self.events.append(dict(frame_number=number, read_begin=begin, read_end=end, reason='QUEUE_CAPACITY_EXCEEDED'))
                raise OverflowError('scan_queue_capacity_exceeded')
            self.items.append(item)
            self.counts['submitted'] += 1
            self.counts['high_water'] = max(self.counts['high_water'], len(self.items))
            self.condition.notify()
            return True

    def take(self, timeout=.02):
        with self.condition:
            if not self.items and not self.closed:
                self.condition.wait(timeout)
            if self.items:
                self.counts['taken'] += 1
                return self.items.popleft()
            return None

    def complete(self, failed=False):
        with self.condition:
            if self.counts['completed'] + self.counts['failed'] >= self.counts['taken']:
                raise ValueError('scan_completion_without_owner')
            self.counts['failed' if failed else 'completed'] += 1

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def discard(self, reason):
        with self.condition:
            self.closed = True
            while self.items:
                number, begin, end, _ = self.items.popleft()
                self.counts['discarded'] += 1
                self.events.append(dict(frame_number=number, read_begin=begin, read_end=end, reason=reason))
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return dict(self.counts, pending=len(self.items), closed=self.closed,
                        capacity=self.capacity, events=list(self.events))

    def finished(self):
        with self.condition:
            return self.closed and not self.items
