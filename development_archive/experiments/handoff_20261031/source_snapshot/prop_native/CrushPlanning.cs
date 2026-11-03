// AGPL-3.0. Exact first-elimination planning using dependency closures.
using System;
using System.Collections.Generic;

partial class FastOriginalSolver {
    Key[] crushClosures;int[][] crushCards;int[] crushCounts;
    HashSet<DynamicKey> crushFailures=new HashSet<DynamicKey>();long crushChecks,crushCacheHits;
    static Key Except(Key a,Key removed){return new Key{A=a.A&~removed.A,B=a.B&~removed.B,C=a.C&~removed.C,D=a.D&~removed.D,E=a.E&~removed.E};}
    void InitCrushPlanning(){
        PrepareGraph();if(crushClosures!=null)return;crushClosures=new Key[n];crushCards=new int[64][];for(int t=0;t<64;t++)crushCards[t]=new int[n];crushCounts=new int[64];
    }
    bool PlanFirstCrush(DynamicKey key,int tray,out Key witness,bool choose){
        InitCrushPlanning();witness=new Key();crushChecks++;
        // PRNG words have no effect until the first elimination itself.
        DynamicKey proof=key;proof.R0=proof.R1=0;
        if(crushFailures.Contains(proof)){crushCacheHits++;return false;}
        int free=7-tray;Array.Clear(crushCounts,0,64);
        for(int i=0;i<n;i++)if(!HasBit(key.Board,i)){
            Key closure=Except(ancestors[i],key.Board);PutBit(ref closure,i);crushClosures[i]=closure;
            if(Count(closure)<=free){int t=Type(key,i);crushCards[t][crushCounts[t]++]=i;}
        }
        double best=double.NegativeInfinity;bool found=false;
        for(int t=1;t<64;t++){
            int need=3-Tray(key,t),count=crushCounts[t];if(count<need)continue;
            int[] cardsOfType=crushCards[t];
            for(int a=0;a<count;a++){
                Key one=crushClosures[cardsOfType[a]];
                if(need==1){if(!choose){witness=one;return true;}ScoreCrush(one,key,t,cardsOfType[a],ref found,ref best,ref witness);continue;}
                for(int b=a+1;b<count;b++){
                    Key two=Union(one,crushClosures[cardsOfType[b]]);if(Count(two)>free)continue;
                    if(need==2){if(!choose){witness=two;return true;}ScoreCrush(two,key,t,cardsOfType[a],ref found,ref best,ref witness);continue;}
                    for(int c=b+1;c<count;c++){
                        Key three=Union(two,crushClosures[cardsOfType[c]]);if(Count(three)>free)continue;
                        if(!choose){witness=three;return true;}ScoreCrush(three,key,t,cardsOfType[a],ref found,ref best,ref witness);
                    }
                }
            }
        }
        if(!found){constraintPrunes++;if(crushFailures.Count>=200000)crushFailures.Clear();crushFailures.Add(proof);}
        return found;
    }
    void ScoreCrush(Key closure,DynamicKey key,int t,int card,ref bool found,ref double best,ref Key witness){
        double score=-Count(closure)*10+Tray(key,t)*1.5+tie[card]*3;
        if(score>best){found=true;best=score;witness=closure;}
    }
}
