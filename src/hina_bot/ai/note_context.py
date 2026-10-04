class NoteContextStore:
    def __init__(self, store):
        self.store = store

    def note(self, key):
        return self.store.note(key)

    def history(self, scope):
        return []


__all__ = ["NoteContextStore"]
