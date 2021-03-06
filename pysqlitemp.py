import sqlite3, multiprocessing, os, io, re
import tabulate, tqdm

# def SimpleTableProcessWrapper(arg):
#     path, tbname, rowids, command=arg
#     pcon=sqlite3.connect(path)
#     stmt=f"SELECT * FROM {tbname} WHERE _ROWID_=?"
#     print(rowids)
#     res=[command(entry) for entry in pcon.executemany(stmt, rowids)]
#     print(res)
#     return res

def MPRowGen(dbpath, stmt):
    pcon=sqlite3.connect(dbpath)
    for entry in pcon.execute(stmt): yield entry

class MPSQLite3Mini:
    def __init__(self, path):
        self.cachestoragepath=path
        self.cachecon=sqlite3.connect(self.cachestoragepath)

    def __setitem__(self, tmptbname, tuples_iter):
        self.cachecon.execute(f"DROP TABLE {tmptbname}")
        first=True
        insertstmt=""
        for entry in tuples_iter:
            if first==True:
                values=",".join(("?" for i in range(0, len(entry))))
                headers=",".join((f"f{i}" for i in range(0, len(entry))))
                self.cachecon.execute(f"DROP TABLE IF EXISTS {tmptbname}")
                self.cachecon.execute(f"CREATE TABLE {tmptbname}({headers})")
                insertstmt=f"INSERT INTO {tmptbname} VALUES({values})"
                self.cachecon.execute(insertstmt, entry)
                break
        self.cachecon.executemany(insertstmt, tuples_iter)
        self.cachecon.commit()

    def __getitem__(self, tmptbname):
        return self.cachecon.execute(f"SELECT * FROM {tmptbname}")

    def __delitem__(self, tmptbname):
        self.cachecon.execute(f"DROP TABLE {tmptbname}")

class MPSQLite3:
    path=None
    con=None

    def __init__(self, path, tmpstoragepath="_temp.db", cachestoragepath="_cache.db"):
        """
        Declare the file path
        """
        self.path=path
        self.con=sqlite3.connect(self.path)
        self.con.row_factory=sqlite3.Row
        self.tmpstoragepath=tmpstoragepath
        self.cachestoragepath=cachestoragepath
        self.cache=MPSQLite3Mini(cachestoragepath)
        self.con.execute("CREATE TABLE IF NOT EXISTS _KeyValue(key UNIQUE,value)")
        self.con.execute("CREATE INDEX IF NOT EXISTS _IDX_KeyValue ON _KeyValue(key)")
        self.con.execute("CREATE TABLE IF NOT EXISTS _KeyBLOB(key UNIQUE,value BLOB)")
        self.con.execute("CREATE INDEX IF NOT EXISTS _IDX_KeyBLOB ON _KeyValue(key)")
        self.con.execute("ATTACH ? AS TMP", (self.tmpstoragepath,))
        self.existingtable=set()
        self.existingtmptable=set()
    def __del__(self):
        self.con.commit()

    def __setitem__(self, k, v): self.SetKV(k,v)
    def __getitem__(self,k):return self.GetKV(k)
    def __delitem__(self,k):self.DelKV(k)

    def ClearTMP(self):
        cachecon=sqlite3.connect(self.tmpstoragepath)
        tables=[t[0] for t in cachecon.execute('SELECT tbl_name FROM TMP.sqlite_master WHERE TYPE="table"')]
        for t in tables:
            cachecon.execute(f"DROP TABLE TMP.{t}")
        cachecon.commit()
    def ClearCache(self):
        os.remove(self.cachestoragepath)

    def QueryExec(self, stmt, args=(), progressbar=True):
        self.con.commit()
        if progressbar==True:
            totstmt=f"SELECT COUNT(1) FROM ({stmt})"
            # print(totstmt)
            for item in self.con.execute(totstmt, args):
                tot=item[0]
            return tqdm.tqdm(self.con.execute(stmt, args), total=tot)
        else: return self.con.execute(stmt)
    
    def QueryExecMany(self, stmt, args=(), progressbar=True, progressbarlen=None):
        self.con.commit()
        if progressbar==True:
            if hasattr(args, '__len__'): progressbarlen=len(args)
            return self.con.executemany(stmt, tqdm.tqdm(args, total=progressbarlen))
        else: return self.con.executemany(stmt, args)

    def QueryPrint(self, stmt, tabulate_tablefmt="orgtbl"):
        self.con.commit()
        cur=self.con.cursor()
        cur.execute(stmt)
        print(tabulate.tabulate(cur, [i[0] for i in cur.description]))

    def PrintTable(self, tablename):
        self.PrintQuery(f"SELECT * FROM {tablename}")

    def SetKV(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO _KeyValue VALUES(?,?)",(key,value))

    def GetKV(self, key):
        self.con.commit()
        for entry in self.con.execute("SELECT value FROM _KeyValue WHERE key=?",(key,)):
            return entry['value']
        return None
    def DelKV(self, key):
        self.con.execute("DELETE FROM _KeyValue WHERE key=?", (key,))

    def SetKBLOB(self, key, BLOB):
        self.con.execute("INSERT OR REPLACE INTO _KeyBLOB VALUES(?,?)",(key,BLOB))
    
    def GetKBLOB(self, key):
        self.con.commit()
        for entry in self.con.execute("SELECT value FROM _KeyBLOB WHERE key=?",(key,)):
            return entry['value']
        return None

    def SetKBLOB_FileHandler(self, key, file):
        self.con.execute("INSERT OR REPLACE INTO _KeyBLOB VALUES(?,?)",(key,file.read()))
    
    def GetKBLOB_FileHandler(self, key):
        self.con.commit()
        for entry in self.con.execute("SELECT value FROM _KeyBLOB WHERE key=?",(key,)):
            return io.BytesIO(entry['value'])
        return None
    
    def SetKBLOB_FilePath(self, key, command=None, remove=False):
        """
        Used for putting file into database after being saved
        Example: 
            model.save("modelname")=>
            SetKBLOB_FilePath("modelname", lambda f: model.save(f), remove=True)
        """
        if command is not None:
            command(key)
        with open(key, "rb") as f:
            self.SetKBLOB_FileHandler(key, f)
        if remove==True:
            os.remove(key)

    def GetKBLOB_FilePath(self, key, command=None, remove=False):
        """
        Used for putting file from database for other commands to read
        Example: 
            model=Glove.load("modelname")=>
            model=GetKBLOB_FilePath("modelname", lambda f: Glove.load(f))
        """
        with open(key, 'wb') as f:
            f.write(self.GetKBLOB(key))
        res=command(key) if command is not None else None
        if remove==True:
            os.remove(key)
        return res

    def DelKBLOB(self, key):
        self.con.execute("DELETE FROM _KeyBLOB WHERE key=?", (key,))

    def chunks(self, lst, n):
        """Yield successive n-sized chunks from lst."""
        for i in range(0, len(lst), n):
            yield lst[i:i + n]
    
    def TableProcess(self, stmt, command=None, progressbar=True, processes=1, mp_chunk=100, storagepath=None):
        """
        set processes=0 to use all available cores
        """
        self.con.commit()
        if storagepath is None: storagepath=self.path
        totstmt=f"SELECT COUNT(1) FROM ({stmt})"
        # print(totstmt)
        for item in self.con.execute(totstmt):
            tot=item[0]
        if command is None:
            for item in tqdm.tqdm(MPRowGen(storagepath, stmt), total=tot):
                yield item
        else:
            if processes==0: processes=multiprocessing.cpu_count()
            if processes==1:
                for item in tqdm.tqdm(MPRowGen(storagepath, stmt), total=tot):
                    yield command(item)
            else:
                with multiprocessing.Pool(processes=processes, maxtasksperchild=mp_chunk) as po:
                    res=po.imap(command, MPRowGen(storagepath, stmt), chunksize=mp_chunk)
                    for r in tqdm.tqdm(res,total=tot): yield r

    def TableProcessSimple(self, tablename, command=None, columns="*", where="", progressbar=True,
                           processes=1, mp_chunk=100, storagepath=None):
        """
        set processes=0 to use all available cores
        """
        self.con.commit()
        if storagepath is None: storagepath=self.path
        stmt=f"SELECT {columns} FROM {tablename} {where}"
        return self.TableProcess(stmt,command, progressbar,processes, mp_chunk,storagepath)

    def TableProcessWithTemp(self, stmt, command=None, tmptbname=None, use_cached=False, progressbar=True, processes=1, mp_chunk=100):
        self.con.commit()
        if command is None: tmptbname="empty_command"
        if tmptbname is None: tmptbname=command.__name__
        create_tmptb=True
        if use_cached==True:
            if tmptbname in self.existingtmptable:
                create_tmptb=False
            else:
                tables=[t[0] for t in self.con.execute('SELECT tbl_name FROM TMP.sqlite_master WHERE TYPE="table"')]
                self.existingtmptable=set(tables)
                if tmptbname in self.existingtmptable:
                    create_tmptb=False
        if create_tmptb:
            self.existingtmptable.add(tmptbname)
            self.con.execute(f"DROP TABLE IF EXISTS TMP.{tmptbname}")
            self.con.execute(f"CREATE TABLE TMP.{tmptbname} AS {stmt}")
        else:
            print("[Info]Cache hit.")
        self.con.commit()
        return self.TableProcessSimple(tmptbname, command, progressbar=progressbar, processes=processes,mp_chunk=mp_chunk,
            storagepath=self.tmpstoragepath)
    
    def CacheSave(self, tuples_iter, tmptbname="empty_task"):
        self.cache[tmptbname]=tuples_iter

    def CacheLoad(self, tmptbname="empty_task"):
        return self.cache[tmptbname]

    def InsertMap(self, datas, tablename):
        sqlitecon=self.con
        if tablename not in self.existingtable:
            sqlitecon.execute(f"CREATE TABLE IF NOT EXISTS {tablename}( id INTEGER PRIMARY KEY AUTOINCREMENT)")
            self.existingtable.add(tablename)
        translated_datas={}
        for k in datas.keys():
            datask=datas[k]
            if (not isinstance(datas[k], int)) and (not isinstance(datas[k], float)):
                datask=str(datask)
                if datask.isdigit():
                    datask=int(datask) 
                elif datask.replace('.','',1).isdigit(): #float number
                    datask=float(datask)
            translated_datas[k.translate({ord(c): "_" for c in "!@#$%^&*()[]{};:,./<>?\|`~-=+"})]=datask
        keys=translated_datas.keys()

        stmt="INSERT OR IGNORE INTO %s(%s) VALUES(%s)"%\
            (tablename, ','.join(keys), ','.join('?'*len(keys)))
        while True:
            try:
                sqlitecon.execute(stmt, [translated_datas[k] for k in translated_datas])
                break
            except sqlite3.OperationalError as e:
                errmsg=str(e)
                if 'has no column named' in errmsg:
                    missingcolname=re.findall(r'has no column named (.*)',errmsg)[0]
                    if isinstance(translated_datas[missingcolname], int):
                        sqlitecon.execute(f'ALTER TABLE {tablename} ADD COLUMN {missingcolname} INTEGER')
                    elif isinstance(translated_datas[missingcolname], float):
                        sqlitecon.execute(f'ALTER TABLE {tablename} ADD COLUMN {missingcolname} REAL')
                    else:
                        sqlitecon.execute(f'ALTER TABLE {tablename} ADD COLUMN {missingcolname} TEXT')
                    sqlitecon.commit()
                    print("[Info] Adding missing column", missingcolname)
                else:
                    print("[Error]", errmsg)