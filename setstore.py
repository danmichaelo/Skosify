from rdflib.store import Store

# Author: Osma Suominen

class IOMemory(Store):
    """\
    An integer-key-optimized context-aware in-memory store.
    
    Uses three dict indices (for subjects, objects and predicates) holding
    sets of triples. Context information is tracked in a separate dict, with
    the triple as key and a dict of {context: quoted} items as value. The
    context information is used to filter triple query results.
    
    Memory usage is low due to several optimizations. RDF nodes are not
    stored directly in the indices; instead, the indices hold integer keys
    and the actual nodes are only stored once in int-to-object and
    object-to-int mapping dictionaries. A default context is determined
    based on the first triple that is added to the store, and no context
    information is actually stored for subsequent other triples with the
    same context information.
    
    Most operations should be quite fast, but a triples() query with two
    bound parts requires a set intersection operation, which may be slow in
    some cases. When multiple contexts are used in the same store, filtering
    based on context has to be done after each query, which may also be
    slow.
    
    """
    context_aware = True
    formula_aware = True

    # The following variable name conventions are used in this class:
    #
    # subject, predicate, object 		unencoded triple parts
    # triple = (subject, predicate, object)	unencoded triple
    # context:					unencoded context
    #
    # sid, pid, oid				integer-encoded triple parts
    # enctriple = (sid, pid, oid)		integer-encoded triple
    # cid					integer-encoded context

    def __init__(self, configuration=None, identifier=None): 
        super(IOMemory, self).__init__() 
        self.__namespace = {} 
        self.__prefix = {} 

        # Mappings for encoding RDF nodes using integer keys, to save memory
        # in the indexes Note that None is always mapped to itself, to make
        # it easy to test for it in either encoded or unencoded form.
        self.__int2obj = {None: None}	# maps integer keys to objects
        self.__obj2int = {None: None}	# maps objects to integer keys

        # Indexes for each triple part, and a list of contexts for each triple
        self.__subjectIndex = {}   # key: sid   	val: enctriple
        self.__predicateIndex = {} # key: pid 		val: enctriple
        self.__objectIndex = {}    # key: oid   	val: enctriple
        self.__tripleContexts = {} # key: enctriple	val: {cid1: quoted, cid2: quoted ...}
        
        # all contexts used in store (unencoded)
        self.__all_contexts = set()
        # default context information for triples
        self.__defaultContexts = None

    def bind(self, prefix, namespace): 
        self.__prefix[namespace] = prefix 
        self.__namespace[prefix] = namespace 

    def namespace(self, prefix): 
        return self.__namespace.get(prefix, None) 

    def prefix(self, namespace): 
        return self.__prefix.get(namespace, None) 

    def namespaces(self): 
        for prefix, namespace in self.__namespace.iteritems(): 
            yield prefix, namespace 

    def add(self, triple, context, quoted=False): 
        Store.add(self, triple, context, quoted)

        if context is not None and context not in self.__all_contexts:
            self.__all_contexts.add(context)
            
        enctriple = self.__encodeTriple(triple)
        sid,pid,oid = enctriple

        self.__addTripleContext(enctriple, context, quoted)

        if sid in self.__subjectIndex: 
            self.__subjectIndex[sid].add(enctriple) 
        else: 
            self.__subjectIndex[sid] = set([enctriple]) 

        if pid in self.__predicateIndex: 
            self.__predicateIndex[pid].add(enctriple) 
        else: 
            self.__predicateIndex[pid] = set([enctriple]) 

        if oid in self.__objectIndex: 
            self.__objectIndex[oid].add(enctriple) 
        else: 
            self.__objectIndex[oid] = set([enctriple]) 


    def remove(self, triplepat, context=None): 
        for triple,contexts in self.triples(triplepat, context):
            enctriple = self.__encodeTriple(triple)
            req_cid = self.__obj2id(context)
            for cid in self.__getTripleContexts(enctriple):
                if context is not None and req_cid != cid:
                    continue
                self.__removeTripleContext(enctriple, cid)
            ctxs = self.__getTripleContexts(enctriple, skipQuoted=True)
            if None in ctxs and (context is None or len(ctxs)==1):
                self.__removeTripleContext(enctriple, None)
            if len(self.__getTripleContexts(enctriple)) == 0:
                # triple has been removed from all contexts
                sid,pid,oid = enctriple
                self.__subjectIndex[sid].remove(enctriple) 
                self.__predicateIndex[pid].remove(enctriple) 
                self.__objectIndex[oid].remove(enctriple) 
                
                del self.__tripleContexts[enctriple]
                
        if triplepat == (None, None, None) and context in self.__all_contexts:
            # remove the whole context
            self.__all_contexts.remove(context)

    def triples(self, triplein, context=None):
        if context is not None:
            if context == self:	# hmm...does this really ever happen?
                context = None

        cid = self.__obj2id(context)
        enctriple = self.__encodeTriple(triplein)
        sid, pid, oid = enctriple
        
        # all triples case (no triple parts given as pattern)
        if sid is None and pid is None and oid is None:
             return self.__all_triples(cid)

        # optimize "triple in graph" case (all parts given)
        if sid is not None and pid is not None and oid is not None:
            if sid in self.__subjectIndex and \
               enctriple in self.__subjectIndex[sid] and \
               self.__tripleHasContext(enctriple, cid):
                return ((triplein, self.__contexts(enctriple)) for i in [0])
            else:
                return self.__emptygen()

        # remaining cases: one or two out of three given
        sets = [] 
        if sid is not None: 
            if sid in self.__subjectIndex: 
                sets.append(self.__subjectIndex[sid]) 
            else: 
                return self.__emptygen() 
        if pid is not None: 
            if pid in self.__predicateIndex: 
                sets.append(self.__predicateIndex[pid]) 
            else: 
                return self.__emptygen() 
        if oid is not None: 
            if oid in self.__objectIndex: 
                sets.append(self.__objectIndex[oid]) 
            else: 
                return self.__emptygen() 
                
        # to get the result, do an intersection of the sets
        return ((self.__decodeTriple(enctriple), self.__contexts(enctriple))
                for enctriple in sets[0].intersection(*sets[1:])
                if self.__tripleHasContext(enctriple,cid))

    def contexts(self, triple=None):
        if triple is None:
            return (context for context in self.__all_contexts)
        
        enctriple = self.__encodeTriple(triple)
        sid,pid,oid = enctriple
        if sid in self.__subjectIndex and enctriple in self.__subjectIndex[sid]:
            return self.__contexts(enctriple)
        else:
            return self.__emptygen()

    def __len__(self, context=None):
        cid = self.__obj2id(context)
        return sum(1 for enctriple,contexts in self.__all_triples(cid))

    # internal utility methods below
    
    def __addTripleContext(self, enctriple, context, quoted):
        """add the given context to the set of contexts for the triple"""
        cid = self.__obj2id(context)
        
        sid, pid, oid = enctriple
        if sid in self.__subjectIndex and enctriple in self.__subjectIndex[sid]:
            # we know the triple exists somewhere in the store
            if enctriple not in self.__tripleContexts:
                # triple exists with default ctx info
                # start with a copy of the default ctx info
                self.__tripleContexts[enctriple] = self.__defaultContexts.copy()

            self.__tripleContexts[enctriple][cid] = quoted
            if not quoted:
                self.__tripleContexts[enctriple][None] = quoted
        else:
            # the triple didn't exist before in the store
            if quoted:	# this context only
                self.__tripleContexts[enctriple] = {cid: quoted}
            else: 	# default context as well
                self.__tripleContexts[enctriple] = {cid: quoted, None: quoted}
        
        # if this is the first ever triple in the store, set default ctx info
        if self.__defaultContexts is None:
            self.__defaultContexts = self.__tripleContexts[enctriple]
        
        # if the context info is the same as default, no need to store it
        if self.__tripleContexts[enctriple] == self.__defaultContexts:
            del self.__tripleContexts[enctriple]
    
    def __getTripleContexts(self, enctriple, skipQuoted=False):
        """return a list of (encoded) contexts for the triple, skipping
           quoted contexts if skipQuoted==True"""

        ctxs = self.__tripleContexts.get(enctriple, self.__defaultContexts)
        
        if not skipQuoted:
            return ctxs.keys()
        
        return [cid for cid,quoted in ctxs.iteritems() if not quoted]

    def __tripleHasContext(self, enctriple, cid):
        """return True iff the triple exists in the given context"""
        ctxs = self.__tripleContexts.get(enctriple, self.__defaultContexts)
        return (cid in ctxs)
    
    def __removeTripleContext(self, enctriple, cid):
        """remove the context from the triple"""
        ctxs = self.__tripleContexts.get(enctriple, self.__defaultContexts).copy()
        del ctxs[cid]
        if ctxs == self.__defaultContexts:
            del self.__tripleContexts[enctriple]
        else:
            self.__tripleContexts[enctriple] = ctxs

    def __obj2id(self, obj):
        """encode object, storing it in the encoding map if necessary, and
           return the integer key"""
        if obj not in self.__obj2int:
            id = randid()
            while id in self.__int2obj: id=randid()
            self.__obj2int[obj] = id
            self.__int2obj[id] = obj
            return id
        return self.__obj2int[obj]
        
    def __encodeTriple(self, triple):
        """encode a whole triple, returning the encoded triple"""
        return tuple(map(self.__obj2id, triple))
 
    def __decodeTriple(self, enctriple):
        """decode a whole encoded triple, returning the original triple"""
        return tuple(map(self.__int2obj.get, enctriple))
    
    def __all_triples(self, cid):
        """return a generator which yields all the triples (unencoded) of
           the given context"""
        for tset in self.__subjectIndex.itervalues(): 
            for enctriple in tset.copy():
                if self.__tripleHasContext(enctriple, cid):
                    yield self.__decodeTriple(enctriple), self.__contexts(enctriple)


    def __contexts(self, enctriple):
        """return a generator for all the non-quoted contexts (unencoded)
           the encoded triple appears in"""
        return (self.__int2obj.get(cid) for cid in self.__getTripleContexts(enctriple, skipQuoted=True) if cid is not None)

    def __emptygen(self): 
        """return an empty generator"""
        if False:
            yield




import random

def randid(randint=random.randint, choice=random.choice, signs=(-1,1)):
    return choice(signs)*randint(1,2000000000)

del random
