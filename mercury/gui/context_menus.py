"""Popup lifetime and click-through dismissal, without replacing widget bindings."""

import tkinter as tk


class ContextMenus:
    def __init__(self, root):
        self.root = root
        self.active = None
        self.retired = {}
        self.closed = False
        self.tag = f"MercuryMenuDismiss{id(self)}"
        self.click_command = root.bind_class(self.tag,"<ButtonPress-1>",self._outside_click)
        self.escape_command = root.bind_class(self.tag,"<Escape>",lambda event:self.dismiss())
        self.map_binding = root.bind("<Map>",self._mapped,add="+")
        self.destroy_binding = root.bind("<Destroy>",self._destroyed,add="+")
        self._tag_tree(root)

    def _tag_tree(self, widget):
        if isinstance(widget,tk.Menu): return
        tags = widget.bindtags()
        if self.tag not in tags: widget.bindtags((self.tag,*tags))
        for child in widget.winfo_children(): self._tag_tree(child)

    def _mapped(self, event):
        if not self.closed and isinstance(event.widget,tk.Misc): self._tag_tree(event.widget)

    def _outside_click(self, event):
        if self.active is not None and not isinstance(event.widget,tk.Menu):
            self.dismiss()
        # Do not return 'break': the same click must reach the underlying control.

    def _prepare(self, menu):
        menu.bind("<Escape>",lambda event:(self.dismiss(),"break")[1])
        end = menu.index("end")
        for i in range(end+1 if end is not None else 0):
            kind = menu.type(i)
            if kind == "cascade":
                self._prepare(menu.nametowidget(menu.entrycget(i,"menu")))
            elif kind == "command":
                command = menu.entrycget(i,"command")
                menu.entryconfigure(i,command=lambda cmd=command:self._invoke(cmd))

    def _invoke(self, command):
        self.dismiss()
        if command: self.root.tk.call("eval",command)

    def show(self, menu, x, y):
        self.dismiss()
        self._tag_tree(self.root)
        self.active = menu
        self._prepare(menu)
        menu.bind("<Unmap>",lambda event:self._retire(menu) if event.widget is menu else None)
        try:
            menu.tk_popup(x,y)
        finally:
            # Tk's popup grab otherwise redirects outside clicks to the menu.
            # Widget bindtags below handle dismissal before their normal action.
            menu.grab_release()

    def _retire(self, menu):
        if self.active is menu: self.active = None
        if menu in self.retired or self.closed: return
        # Tk's MenuInvoke unposts before invoking its Tcl command. Keep the
        # widget and registered callbacks alive until that event has completed.
        self.retired[menu] = self.root.after_idle(lambda:self._dispose(menu))

    def _dispose(self, menu):
        self.retired.pop(menu,None)
        if menu.winfo_exists(): menu.destroy()

    def dismiss(self):
        menu = self.active
        if menu is None: return
        if menu.winfo_exists():
            if menu.winfo_ismapped():
                self.root.tk.call("tk::MenuUnpost",menu._w)
            else:
                menu.unpost()
        self._retire(menu)

    def _destroyed(self, event):
        if event.widget is self.root: self.close()

    def close(self):
        if self.closed: return
        self.dismiss()
        self.closed = True
        for menu,job in list(self.retired.items()):
            self.root.after_cancel(job)
            self._dispose(menu)
        self.root.unbind("<Map>",self.map_binding)
        self.root.unbind("<Destroy>",self.destroy_binding)
        for sequence,command in [("<ButtonPress-1>",self.click_command),("<Escape>",self.escape_command)]:
            self.root.unbind_class(self.tag,sequence)
            self.root.deletecommand(command)
