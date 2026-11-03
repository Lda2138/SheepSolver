// AGPL-3.0. Reduce dynamic-state entropy by fixing accessible changing cards.
using System;

partial class FastOriginalSolver {
    void FreezeOrder(int[] head,int count,int depth){
        InitGuidance();double[] scores=guidanceScores[depth];
        for(int h=0;h<count;h++){
            int i=head[h],t=currentTypes[i],held=types[t];
            double s=cards[i].Layer*.8+(held==2?400:held==1?15:0)+tie[i]*3;
            if(held==0)s-=Math.Max(0,orderCount-2)*10;
            foreach(int changing in colors.Special)if(!picked[changing]&&parents[changing]<=8){
                if(i==changing)s+=180;
                else if(HasBit(ancestors[changing],i))s+=(100+goalWeight*40)/(1+parents[changing]);
            }
            foreach(int child in cards[i].Children)if(!picked[child]&&parents[child]==1)s+=types[currentTypes[child]]==2?30:2;
            scores[h]=s;
        }
        for(int h=1;h<count;h++){int card=head[h],j=h-1;double value=scores[h];while(j>=0&&scores[j]<value){head[j+1]=head[j];scores[j+1]=scores[j];j--;}head[j+1]=card;scores[j+1]=value;}
    }
}
