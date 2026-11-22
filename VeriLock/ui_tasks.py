"""Run slow operations in background threads, then resume on the Tkinter thread."""
import functools
import queue
import threading


def background_callback(owner, window):
    def decorate(function):
        @functools.wraps(function)
        def start(*args, **kwargs):
            if getattr(owner, '_closing', False) or getattr(owner, '_ui_task_busy', False):
                return
            owner._ui_task_busy = True
            session = getattr(owner, '_admin_session_generation', 0)
            routine = function(*args, **kwargs)

            def valid():
                return (not getattr(owner, '_closing', False)
                        and session == getattr(owner, '_admin_session_generation', 0)
                        and window.winfo_exists())

            def advance(value=None, error=None):
                try:
                    if not valid():
                        routine.close()
                        owner._ui_task_busy = False
                        return
                    action = routine.throw(error) if error is not None else routine.send(value)
                except StopIteration:
                    owner._ui_task_busy = False
                    return
                except Exception:
                    owner._ui_task_busy = False
                    raise
                mailbox = queue.Queue(maxsize=1)

                def run():
                    try:
                        mailbox.put((action(), None))
                    except Exception as failure:
                        mailbox.put((None, failure))

                threading.Thread(target=run, daemon=True, name='verilock-admin-task').start()

                def poll():
                    try:
                        result, failure = mailbox.get_nowait()
                    except queue.Empty:
                        # Keep checking until the operation finishes, but never
                        # touch widgets or resume a closed window or revoked admin session.
                        if not getattr(owner, '_closing', False):
                            owner.root.after(25, poll)
                        return
                    advance(result, failure)

                owner.root.after(25, poll)

            advance()
        return start
    return decorate


def background_method(function):
    @functools.wraps(function)
    def start(self, *args, **kwargs):
        if getattr(self, '_ui_task_busy', False):
            if not getattr(self, '_closing', False) and self.admin_face_active:
                self.admin_face_window.after(30, lambda: start(self, *args, **kwargs))
            return
        return background_callback(self, self.admin_face_window)(function)(self, *args, **kwargs)
    return start
