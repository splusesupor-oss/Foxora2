            # A flock file is a stable lock inode.  Never unlink it: another
            # process can acquire the inode between LOCK_UN and unlink, after
            # which a third process would create/lock a different inode.
            # The PID record may remain stale; flock is authoritative and the
            # next owner replaces the record after acquiring the lock.
            try:
                self._fcntl.flock(self.stream.fileno(), self._fcntl.LOCK_UN)
            except OSError:
                pass
            self._close_stream()
        self.mode = None
