"""Product table and clickable validation messages."""

import tkinter as tk
from tkinter import ttk


class ResultsPanel(ttk.Notebook):
    def __init__(self,parent,on_select):
        super().__init__(parent)
        self.on_select = on_select
        products = ttk.Frame(self,padding=6)
        messages = ttk.Frame(self,padding=6)
        self.add(products,text='Products')
        self.add(messages,text='Validation & messages')
        columns = ('name','role','flow','o2','n2','temperature','pressure','flow_check','composition_check')
        self.table = ttk.Treeview(products,columns=columns,show='headings',height=5)
        headings = ['Product','Target','Flow (slpm)','O2 (mol%)','N2 (mol%)','Temp (°C, assumed)','Pressure (psia)','Flow target','Composition target']
        for key,label in zip(columns,headings):
            self.table.heading(key,text=label)
            self.table.column(key,width=150 if key in ('name','temperature','composition_check') else 120,
                              minwidth=90,anchor='w' if key in ('name','role') else 'center')
        scroll=ttk.Scrollbar(products,orient='vertical',command=self.table.yview)
        xscroll=ttk.Scrollbar(products,orient='horizontal',command=self.table.xview)
        self.table.configure(yscrollcommand=scroll.set,xscrollcommand=xscroll.set)
        self.table.grid(row=0,column=0,sticky='nsew')
        scroll.grid(row=0,column=1,sticky='ns')
        xscroll.grid(row=1,column=0,sticky='ew')
        products.rowconfigure(0,weight=1); products.columnconfigure(0,weight=1)
        self.table.tag_configure('pass',foreground='#246139')
        self.table.tag_configure('fail',foreground='#a32e3e')
        self.table.bind('<<TreeviewSelect>>',self._selected_product)
        self.messages=tk.Listbox(messages,height=6,relief='flat',background='#f6f8fb',foreground='#34475d',
                                 highlightthickness=0,selectmode='browse')
        bar=ttk.Scrollbar(messages,orient='vertical',command=self.messages.yview)
        xbar=ttk.Scrollbar(messages,orient='horizontal',command=self.messages.xview)
        self.messages.configure(yscrollcommand=bar.set,xscrollcommand=xbar.set)
        self.messages.grid(row=0,column=0,sticky='nsew')
        bar.grid(row=0,column=1,sticky='ns')
        xbar.grid(row=1,column=0,sticky='ew')
        messages.rowconfigure(0,weight=1); messages.columnconfigure(0,weight=1)
        self.issue_targets=[]
        self.messages.bind('<<ListboxSelect>>',self._selected_issue)

    def clear(self):
        self.table.delete(*self.table.get_children())
        self.messages.delete(0,'end')
        self.issue_targets=[]

    def show_issues(self,issues):
        self.messages.delete(0,'end')
        self.issue_targets=[]
        for issue in issues:
            self.messages.insert('end',issue.message.replace('\n',' — '))
            self.issue_targets.append(issue.node_id or issue.connection_id)
        self.select(1)

    def show_result(self,result):
        self.clear()
        for key,product in result.products.items():
            s=product.stream
            check=lambda value: '—' if value is None else 'PASS' if value else 'UNMET'
            tag='pass' if product.passed else 'fail' if product.passed is False else ''
            self.table.insert('', 'end',iid=key,values=(product.name,product.role,f'{s.flow_slpm:,.3f}',
                              f'{100*s.oxygen:.4f}',f'{100*(1-s.oxygen):.4f}',f'{s.temperature_c:g}',f'{s.pressure_psia:.3f}',
                              check(product.flow_met),check(product.composition_met)),tags=(tag,))
        self.messages.insert('end',f'External flow balance error: {result.flow_balance_error_slpm:.3g} slpm; '
                             f'O2 balance error: {result.oxygen_balance_error_slpm:.3g} slpm')
        self.issue_targets.append(None)
        for warning in result.warnings:
            self.messages.insert('end',warning)
            self.issue_targets.append(None)
        self.select(0)

    def _selected_product(self,event=None):
        selection=self.table.selection()
        if selection: self.on_select(selection[0])

    def _selected_issue(self,event=None):
        selection=self.messages.curselection()
        if selection and selection[0]<len(self.issue_targets):
            target=self.issue_targets[selection[0]]
            if target: self.on_select(target)
