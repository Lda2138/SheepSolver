// AGPL-3.0. Goal-directed ordering with a cheap, sound two-slot constraint.
using System;
using System.Linq;

partial class FastOriginalSolver {
    int[] goalOne,goalTwo,goalCostOne,goalCostTwo,headTypes;double[][] guidanceScores;double goalWeight;
    void InitGuidance(){
        if(goalOne!=null)return;PrepareGraph();goalOne=new int[64];goalTwo=new int[64];goalCostOne=new int[64];goalCostTwo=new int[64];headTypes=new int[64];
        guidanceScores=Enumerable.Range(0,n).Select(_=>new double[n]).ToArray();goalWeight=.6+random.NextDouble()*1.2;
    }
    Key CurrentTypeMask(int t){Key mask=ordinaryMasks[t];foreach(int i in colors.Special)if(!picked[i]&&currentTypes[i]==t)PutBit(ref mask,i);return mask;}
    bool CanCrushWithTwoSlots(){
        int free=7-trayCount;
        for(int t=1;t<64;t++)if(types[t]>0){
            int need=3-types[t];if(need>free)continue;Key same=CurrentTypeMask(t);int viable=0;
            for(int i=0;i<n;i++)if(!picked[i]&&currentTypes[i]==t&&OtherCost(ancestors[i],pickedBits,same)+need<=free){if(++viable>=need)return true;}
        }
        constraintPrunes++;return false;
    }
    void GuideOrder(int[] head,int count,int depth){
        InitGuidance();Array.Clear(headTypes,0,64);for(int h=0;h<count;h++)headTypes[currentTypes[head[h]]]++;
        for(int t=0;t<64;t++){goalOne[t]=goalTwo[t]=-1;goalCostOne[t]=goalCostTwo[t]=10000;}
        for(int i=0;i<n;i++)if(!picked[i]){
            int t=currentTypes[i];if(types[t]==0)continue;int cost=parents[i];
            if(cost<goalCostOne[t]){goalTwo[t]=goalOne[t];goalCostTwo[t]=goalCostOne[t];goalOne[t]=i;goalCostOne[t]=cost;}
            else if(cost<goalCostTwo[t]){goalTwo[t]=i;goalCostTwo[t]=cost;}
        }
        Key plan;bool planned=PlanFirstCrush(DynamicState(),trayCount,out plan,true);
        double[] scores=guidanceScores[depth];
        for(int h=0;h<count;h++){
            int i=head[h],t=currentTypes[i],held=types[t];
            double s=cards[i].Layer*(depth<first*n?1.5:.25)+(held==2?1000:held==1?20:0)+
                (headTypes[t]+held>=3?30:0)+(isSpecial[i]?2:0)+tie[i]*2;
            if(planned&&HasBit(plan,i))s+=45;
            if(held==0)s-=Math.Max(0,orderCount-2)*12;
            for(int u=1;u<64;u++)if(types[u]>0){
                int target=goalOne[u];if(target>=0&&HasBit(ancestors[target],i))s+=goalWeight*(types[u]==2?60:22)/(1+goalCostOne[u]);
                target=goalTwo[u];if(types[u]==1&&target>=0&&HasBit(ancestors[target],i))s+=goalWeight*22/(1+goalCostTwo[u]);
            }
            foreach(int child in cards[i].Children)if(!picked[child]&&parents[child]==1){int wanted=types[currentTypes[child]];s+=wanted==2?35:wanted==1?12:1;}
            scores[h]=s;
        }
        for(int h=1;h<count;h++){int card=head[h],j=h-1;double value=scores[h];while(j>=0&&scores[j]<value){head[j+1]=head[j];scores[j+1]=scores[j];j--;}head[j+1]=card;scores[j+1]=value;}
    }
}
