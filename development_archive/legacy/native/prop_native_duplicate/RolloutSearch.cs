// AGPL-3.0. Nested Rollout Policy Adaptation for the single-player puzzle.
// Every rollout uses the same exact cover, tray and seeded-color transition.
using System;

partial class FastOriginalSolver {
    long rollouts;int nrpaLevel=2,policyEpoch;
    sealed class RolloutPlan {public int[] Cards,Types;public int Length;public double Quality;}
    int[] actionCards;double[] actionWeights;int[] actionAvailable;Scratch rolloutScratch;
    double layerBias,sideBias,colorBias,pairBias,temperature,priorityAfter;

    int PolicyFeature(DynamicKey key,int card,int tray){return card*9+Tray(key,Type(key,card))*3+Math.Min(2,tray/2);}
    int Actions(DynamicKey key,int tray,double[] policy){
        int count=0,distinct=0;Array.Clear(actionAvailable,0,64);
        for(int t=1;t<64;t++)if(Tray(key,t)>0)distinct++;
        for(int i=0;i<n;i++)if(!HasBit(key.Board,i)&&!Unremoved(covers[i],key.Board)){
            int t=Type(key,i),held=Tray(key,t);
            if((tray==6&&held!=2)||(distinct==5&&held==0))continue;
            DynamicKey next;int nextType,nextTray;Key witness;
            if(!Step(key,i,tray,out next,out nextType,out nextTray))continue;
            if(Count(next.Board)<n&&!PlanFirstCrush(next,nextTray,out witness,false))continue;
            actionCards[count++]=i;actionAvailable[t]++;
        }
        double max=double.NegativeInfinity;
        for(int j=0;j<count;j++){
            int i=actionCards[j],t=Type(key,i),held=Tray(key,t);
            int depth=Count(key.Board);double factor=depth>=priorityAfter?1:.08;
            double score=policy[PolicyFeature(key,i,tray)]+factor*((held==2?pairBias:held==1?2.1:0)+
                (actionAvailable[t]+held>=3?2.5:0)+Math.Min(3,actionAvailable[t])*.35)+
                cards[i].Layer*layerBias+(HasBit(sideMask,i)?sideBias:0)+(isSpecial[i]?colorBias:0)-
                (held==0?tray*.35*factor:0);
            actionWeights[j]=score/temperature;if(actionWeights[j]>max)max=actionWeights[j];
        }
        for(int j=0;j<count;j++)actionWeights[j]=Math.Exp(actionWeights[j]-max);
        return count;
    }
    bool Budget(){
        if(clock.Elapsed.TotalSeconds-lastReport>=.5)Report("searching");
        if(timeout||clock.Elapsed.TotalSeconds>limit){timeout=true;if(stopReason=="")stopReason="time_limit";return false;}
        return true;
    }
    RolloutPlan SamplePolicy(double[] policy){
        var plan=new RolloutPlan{Cards=new int[n],Types=new int[n],Length=startDepth};
        Array.Copy(result,plan.Cards,startDepth);Array.Copy(resultTypes,plan.Types,startDepth);
        DynamicKey key=DynamicState();int tray=trayCount;
        for(int depth=startDepth;depth<n;depth++){
            int count=Actions(key,tray,policy);if(count==0)break;
            double sum=0;for(int j=0;j<count;j++)sum+=actionWeights[j];double pick=random.NextDouble()*sum;
            int chosen=count-1;for(int j=0;j<count;j++){pick-=actionWeights[j];if(pick<=0){chosen=j;break;}}
            int i=actionCards[chosen],t,nextTray;DynamicKey next;
            nodes++;if(!Step(key,i,tray,out next,out t,out nextTray))break;
            key=next;tray=nextTray;plan.Cards[depth]=i;plan.Types[depth]=t;plan.Length=depth+1;
            // Check the first-triple bound on the chosen path, rather than on
            // every sibling. One rollout stays cheap while rejecting dead ends.

        }
        rollouts++;plan.Quality=plan.Length-tray*.05;
        if(plan.Length>deepest){deepest=plan.Length;Array.Copy(plan.Cards,bestPrefix,deepest);}
        return plan;
    }
    void AdaptPolicy(double[] policy,RolloutPlan plan){
        DynamicKey key=DynamicState();int tray=trayCount;const double alpha=.35;
        for(int depth=startDepth;depth<plan.Length;depth++){
            int count=Actions(key,tray,policy);if(count==0)break;
            double sum=0;for(int j=0;j<count;j++)sum+=actionWeights[j];
            for(int j=0;j<count;j++){
                int feature=PolicyFeature(key,actionCards[j],tray);
                policy[feature]=Math.Max(-15,Math.Min(15,policy[feature]-alpha*actionWeights[j]/sum));
            }
            int selected=plan.Cards[depth],f=PolicyFeature(key,selected,tray);
            policy[f]=Math.Min(15,policy[f]+alpha);
            DynamicKey next;int t,nextTray;if(!Step(key,selected,tray,out next,out t,out nextTray))break;
            key=next;tray=nextTray;
        }
    }
    RolloutPlan NestedPolicy(int level,double[] parent){
        if(level==0)return SamplePolicy(parent);
        double[] policy=(double[])parent.Clone();RolloutPlan best=null;int iterations=level==1?16:8;
        for(int k=0;k<iterations&&Budget();k++){
            RolloutPlan candidate=NestedPolicy(level-1,policy);
            if(candidate!=null&&(best==null||candidate.Quality>best.Quality))best=candidate;
            if(best!=null&&best.Length==n)return best;
            if(best!=null)AdaptPolicy(policy,best);
        }
        return best;
    }
    bool RolloutSearch(){
        PrepareGraph();actionCards=new int[n];actionWeights=new double[n];actionAvailable=new int[64];rolloutScratch=new Scratch();
        while(Budget()){
            policyEpoch++;layerBias=.6+random.NextDouble()*1.8;sideBias=(random.NextDouble()-.5)*2;
            colorBias=random.NextDouble();pairBias=4+random.NextDouble()*8;temperature=.5+random.NextDouble();priorityAfter=n*(.3+random.NextDouble()*.65);
            var candidate=NestedPolicy(nrpaLevel,new double[n*9]);
            if(candidate!=null&&candidate.Length==n){Array.Copy(candidate.Cards,result,n);Array.Copy(candidate.Types,resultTypes,n);return true;}
        }
        return false;
    }
}
