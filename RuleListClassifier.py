from sklearn.base import BaseEstimator
from sklearn.utils import check_X_y
import sklearn.metrics
import sys
import numpy as np
import pandas as pd
from LethamBRL.BRL_code import *
from Discretization.MDLP import *

class RuleListClassifier(BaseEstimator):
    """
    This is a scikit-learn compatible wrapper for the Bayesian Rule List
    classifier developed by Benjamin Letham. It produces a highly
    interpretable model (a list of decision rules) of the same form as
    an expert system. 

    Parameters
    ----------
    listlengthprior : int, optional (default=3)
        Prior hyperparameter for expected list length (excluding null rule)

    listwidthprior : int, optional (default=1)
        Prior hyperparameter for expected list length (excluding null rule)
        
    maxcardinality : int, optional (default=1)
        Maximum cardinality of an itemset
        
    minsupport : int, optional (default=10)
        Minimum support (%) of an itemset

    alpha : array_like, shape = [n_classes]
        prior hyperparameter for multinomial pseudocounts

    n_chains : int, optional (default=3)
        Number of MCMC chains for inference

    max_iter : int, optional (default=50000)
        Maximum number of iterations
        
    class1label: str, optional (default="class 1")
        Label or description of what class 1 means
        
    verbose: bool, optional (default=True)
        Verbose output
    """
    
    def __init__(self, listlengthprior=3, listwidthprior=1, maxcardinality=2, minsupport=10, alpha = np.array([1.,1.]), n_chains=3, max_iter=50000, class1label="class 1", verbose=True):
        self.listlengthprior = listlengthprior
        self.listwidthprior = listwidthprior
        self.maxcardinality = maxcardinality
        self.minsupport = minsupport
        self.alpha = alpha
        self.n_chains = n_chains
        self.max_iter = max_iter
        self.class1label = class1label
        self.verbose = verbose
        
        self.thinning = 1 #The thinning rate
        self.burnin = self.max_iter//2 #the number of samples to drop as burn-in in-simulation
        
        self.discretizer = None
        self.d_star = None
        
    
    def fit2(self, X, y, feature_labels=None, **kwargs):
        X, y = check_X_y(X, y, ensure_min_samples=2, estimator=self)
        if feature_labels == None:
            feature_labels = ["ft"+str(i+1) for i in range(len(X[0]))]
        self.feature_labels = feature_labels
        
        #We will store here the MCMC results
        permsdic = defaultdict(default_permsdic) 
        data = list(X[:])
        itemsets = [r[0] for r in fpgrowth(data, supp=self.minsupport, zmax=self.maxcardinality)]
        itemsets = list(set(itemsets))
        print len(itemsets),'rules mined'
        
        X = [set(range(len(data)))]
        for item in itemsets:
            X.append(set([i for (i,xi) in enumerate(data) if set(item).issubset(xi)]))
        
        #now form lhs_len
        lhs_len = [0]
        for lhs in itemsets:
            lhs_len.append(len(lhs))
        nruleslen = Counter(lhs_len)
        lhs_len = array(lhs_len)
        itemsets_all = ['null']
        itemsets_all.extend(itemsets)
        
        
        Xtrain, Ytrain = X, np.vstack((y, 1-y)).T.astype(int)
        self.itemsets = itemsets_all
        
        #Do MCMC
        res, Rhat = run_bdl_multichain_serial(self.max_iter,
                                              self.thinning,
                                              self.alpha,
                                              self.listlengthprior,
                                              self.listwidthprior,
                                              Xtrain,Ytrain,
                                              nruleslen,
                                              lhs_len,
                                              self.maxcardinality,
                                              permsdic,
                                              self.burnin,
                                              self.n_chains,
                                              [None]*self.n_chains, 
                                              verbose=self.verbose)
            
        #Merge the chains
        permsdic = merge_chains(res)
        
        ###The point estimate, BRL-point
        self.d_star = get_point_estimate(permsdic,
                                         lhs_len,
                                         Xtrain,
                                         Ytrain,
                                         self.alpha,
                                         nruleslen,
                                         self.maxcardinality,
                                         self.listlengthprior,
                                         self.listwidthprior, 
                                         verbose=self.verbose)
        
        if self.d_star:
            #Compute the rule consequent
            self.theta, self.ci_theta = get_rule_rhs(Xtrain,
                                                     Ytrain,
                                                     self.d_star,
                                                     self.alpha,
                                                     True)
            
        return self
        
    
    def fit(self, X, y, feature_labels = None): # -1 for unlabeled
        """Fit rule lists to data

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Training data 

        y : array_like, shape = [n_samples]
            Labels
            
        feature_labels : array_like, shape = [n_features], optional (default: None)
            String labels for each feature. If none, features are simply enumerated

        Returns
        -------
        self : returns an instance of self.
        """
        if len(set(y)) != 2:
            raise Exception("Only binary classification is supported at this time!")
        X, y = check_X_y(X, y, ensure_min_samples=2, estimator=self)
        
        if feature_labels == None:
            feature_labels = ["ft"+str(i+1) for i in range(len(X[0]))]
        self.feature_labels = feature_labels
        
        if type(X) != list:
            X = np.array(X).tolist()
        if 'str' not in str(type(X[0][0])):
            if self.verbose:
                print "Warning: non-categorical data. Trying to discretize. (Please convert categorical values to strings to avoid this.)"
            X = self.discretize(X, y)
        
        permsdic = defaultdict(default_permsdic) #We will store here the MCMC results
        
        data = list(X[:])
        #Now find frequent itemsets
        #Mine separately for each class
        data_pos = [x for i,x in enumerate(data) if y[i]==0]
        data_neg = [x for i,x in enumerate(data) if y[i]==1]
        assert len(data_pos)+len(data_neg) == len(data)
        try:
            itemsets = [r[0] for r in fpgrowth(data_pos,supp=self.minsupport,zmax=self.maxcardinality)]
            itemsets.extend([r[0] for r in fpgrowth(data_neg,supp=self.minsupport,zmax=self.maxcardinality)])
        except TypeError:
            itemsets = [r[0] for r in fpgrowth(data_pos,supp=self.minsupport,max=self.maxcardinality)]
            itemsets.extend([r[0] for r in fpgrowth(data_neg,supp=self.minsupport,max=self.maxcardinality)])
        itemsets = list(set(itemsets))
        if self.verbose:
            print len(itemsets),'rules mined'
        #Now form the data-vs.-lhs set
        #X[j] is the set of data points that contain itemset j (that is, satisfy rule j)
        X = [ set() for j in range(len(itemsets)+1)]
        X[0] = set(range(len(data))) #the default rule satisfies all data
        for (j,lhs) in enumerate(itemsets):
            X[j+1] = set([i for (i,xi) in enumerate(data) if set(lhs).issubset(xi)])
        #now form lhs_len
        lhs_len = [0]
        for lhs in itemsets:
            lhs_len.append(len(lhs))
        nruleslen = Counter(lhs_len)
        lhs_len = array(lhs_len)
        itemsets_all = ['null']
        itemsets_all.extend(itemsets)
        
        Xtrain,Ytrain,nruleslen,lhs_len,self.itemsets = (X,np.vstack((y, 1-y)).T.astype(int),nruleslen,lhs_len,itemsets_all)
            
        #Do MCMC
        res,Rhat = run_bdl_multichain_serial(self.max_iter,self.thinning,self.alpha,self.listlengthprior,self.listwidthprior,Xtrain,Ytrain,nruleslen,lhs_len,self.maxcardinality,permsdic,self.burnin,self.n_chains,[None]*self.n_chains, verbose=self.verbose)
            
        #Merge the chains
        permsdic = merge_chains(res)
        
        ###The point estimate, BRL-point
        self.d_star = get_point_estimate(permsdic,lhs_len,Xtrain,Ytrain,self.alpha,nruleslen,self.maxcardinality,self.listlengthprior,self.listwidthprior, verbose=self.verbose) #get the point estimate
        
        if self.d_star:
            #Compute the rule consequent
            self.theta, self.ci_theta = get_rule_rhs(Xtrain,Ytrain,self.d_star,self.alpha,True)
            
        return self
    
    def discretize(self, X, y):
        D = pd.DataFrame(np.hstack(( X, np.array(y).reshape((len(y), 1)) )), columns=list(self.feature_labels)+["y"])
        self.discretizer = MDLP_Discretizer(dataset=D, class_label="y")
        return self._prepend_feature_labels(np.array(self.discretizer._data)[:, :-1])
    
    def _prepend_feature_labels(self, X):
        Xl = np.copy(X).astype(str).tolist()
        for i in range(len(Xl)):
            for j in range(len(Xl[0])):
                Xl[i][j] = self.feature_labels[j]+" : "+Xl[i][j]
        return Xl
    
    def __str__(self):
        return self.tostring(decimals=1)
        
    def tostring(self, decimals=1):
        if self.d_star:
            detect = ""
            if self.class1label != "class 1":
                detect = "for detecting "+self.class1label
            header = "Trained RuleListClassifier "+detect+"\n"
            separator = "".join(["="]*len(header))+"\n"
            s = ""
            for i,j in enumerate(self.d_star):
                if self.itemsets[j] != 'null':
                    condition = "ELSE IF "+(" AND ".join([self.itemsets[j][k] for k in range(len(self.itemsets[j]))])) + " THEN"
                else:
                    condition = "ELSE"
                s += condition + " probability of "+self.class1label+": "+str(np.round(self.theta[i]*100,decimals)) + "% ("+str(np.round(self.ci_theta[i][0]*100,decimals))+"%-"+str(np.round(self.ci_theta[i][1]*100,decimals))+"%)\n"
            return header+separator+s[5:]+separator[1:]
        else:
            return "(Untrained RuleListClassifier)"
        
    def _to_itemset_indices(self, data):
        #X[j] is the set of data points that contain itemset j (that is, satisfy rule j)
        X = [set() for j in range(len(self.itemsets))]
        X[0] = set(range(len(data))) #the default rule satisfies all data
        for (j,lhs) in enumerate(self.itemsets):
            if j>0:
                X[j] = set([i for (i,xi) in enumerate(data) if set(lhs).issubset(xi)])
        return X
        
    def predict_proba(self, X):
        """Compute probabilities of possible outcomes for samples in X.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]

        Returns
        -------
        T : array-like, shape = [n_samples, n_classes]
            Returns the probability of the sample for each class in
            the model. The columns correspond to the classes in sorted
            order, as they appear in the attribute `classes_`.
        """
        
        if self.discretizer != None:
            self.discretizer._data = pd.DataFrame(X, columns=self.feature_labels)
            self.discretizer.apply_cutpoints()
            D = self._prepend_feature_labels(np.array(self.discretizer._data)[:, :-1])
        else:
            D = X
        
        N = len(D)
        X = self._to_itemset_indices(D[:])
        P = preds_d_t(X, np.zeros((N, 1), dtype=int),self.d_star,self.theta)
        return np.vstack((1-P, P)).T
        
    def predict(self, X):
        """Perform classification on samples in X.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]

        Returns
        -------
        y_pred : array, shape = [n_samples]
            Class labels for samples in X.
        """
        return 1*(self.predict_proba(X)[:,0]>=0.5)
    
    def score(self, X, y, sample_weight=None):
        return sklearn.metrics.accuracy_score(y, self.predict(X), sample_weight=sample_weight)
    
if __name__ == "__main__":
    from examples.demo import *