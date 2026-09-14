class NoteContext:
    def __init__(self, store):
        self.store = store

    def note(self, key):
        return self.store.note(key)


__all__ = ["NoteContext"]
