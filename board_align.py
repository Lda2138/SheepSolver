"""Fit complete-map geometry to pale tile faces. Never chooses game moves."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from desktop_assistant import overlap

FACE_OFFSETS=((-12,-13),(12,-13),(-12,12),(12,12),(-14,-6),(14,-6),(-14,5),(14,5))


def face_offsets(calibration):
    """Keep the visibility gate on the tile frame when the game scales its grid."""
    sx=calibration.get('face_scale_x',1.0);sy=calibration.get('face_scale_y',1.0)
    return [(round(dx*sx),round(dy*sy)) for dx,dy in FACE_OFFSETS]


def _face_centers(pale):
    """Connected pale frames, excluding small icon fragments and covered strips."""
    from collections import deque
    mask=pale.copy();mask[:118]=False;mask[466:]=False;mask[:,:20]=False;mask[:,316:]=False
    centers=[]
    for y,x in zip(*np.where(mask)):
        if not mask[y,x]:continue
        queue=deque([(int(x),int(y))]);mask[y,x]=False
        xlo=xhi=int(x);ylo=yhi=int(y);count=0
        while queue:
            xx,yy=queue.popleft();count+=1
            xlo=min(xlo,xx);xhi=max(xhi,xx);ylo=min(ylo,yy);yhi=max(yhi,yy)
            for nx,ny in ((xx-1,yy),(xx+1,yy),(xx,yy-1),(xx,yy+1)):
                if 20<=nx<316 and 118<=ny<466 and mask[ny,nx]:
                    mask[ny,nx]=False;queue.append((nx,ny))
        if count>=130 and 19<=xhi-xlo<=41 and 21<=yhi-ylo<=43:
            centers.append(((xlo+xhi)/2,(ylo+yhi)/2))
    return np.asarray(centers,dtype=float).reshape((-1,2))


def _component_seed(col,row,centers):
    """Register all exposed map tiles, with one distinct observed frame per tile."""
    if len(centers)<len(col):return None
    coords=np.column_stack((col,row));params=[]
    for axis in range(2):
        a=coords[:,axis];b=centers[:,axis]
        if np.ptp(a)==0:return None
        if len(a)==len(b):
            origin,unit=np.linalg.lstsq(np.column_stack((np.ones(len(a)),np.sort(a))),np.sort(b),rcond=None)[0]
        else:
            unit=np.ptp(b)/np.ptp(a);origin=b.min()-unit*a.min()
        params.append((origin,unit))
    origin=np.array([p[0] for p in params]);unit=np.array([p[1] for p in params])
    for _ in range(2):
        points=origin+coords*unit
        distances=((points[:,None,:]-centers[None,:,:])**2).sum(axis=2)
        nearest=distances.argmin(axis=1)
        if len(set(nearest))!=len(coords) or distances.min(axis=1).max()>9:return None
        for axis in range(2):
            origin[axis],unit[axis]=np.linalg.lstsq(np.column_stack((np.ones(len(col)),coords[:,axis])),centers[nearest,axis],rcond=None)[0]
    if not all(2.7<=u<=5.5 for u in unit):return None
    return np.array([origin[0],origin[1],unit[0],unit[1]])


def _axis_seeds(coords,observed):
    """Vote for grid transforms despite split icons and missing pale frames.

    These are candidates only: fit still checks the frame of EVERY exposed
    card, and callers independently reject cards outside the map silhouette.
    """
    if not len(observed):return []
    choices=[]
    for unit in np.arange(2.7,5.501,.02):
        origins=(observed[:,None]-unit*coords[None,:]).ravel()
        bins,counts=np.unique(np.rint(origins).astype(int),return_counts=True)
        for index in np.argsort(counts)[-4:]:
            group=origins[np.abs(origins-bins[index])<=.75]
            origin=float(np.median(group))
            distances=np.abs(origin+unit*coords[:,None]-observed[None,:]).min(axis=1)
            choices.append((int((distances<=2.5).sum()),-float(np.minimum(distances,5).sum()),origin,float(unit)))
    choices.sort(reverse=True);kept=[]
    for _,_,origin,unit in choices:
        if any(abs(origin-o)<1 and abs(unit-u)<.03 for o,u in kept):continue
        kept.append((origin,unit))
        if len(kept)>=48:break
    return kept


def visible_face_count(image_path):
    im=np.asarray(Image.open(image_path).convert('RGB'))
    return len(_face_centers((im[:,:,0]>205)&(im[:,:,1]>205)&(im[:,:,2]>145)))


def exposed(cards):
    return [c for c in cards if not any(b['layer']>c['layer'] and overlap(c,b) for b in cards)]


def energy_face(array,point):
    x,y=point;patch=array[y-13:y+14,x-11:x+12]
    yellow=((patch[:,:,0]>200)&(patch[:,:,1]>150)&(patch[:,:,2]<125)).sum()
    outline=sum(array[y+dy,x+dx,0]<180 and array[y+dy,x+dx,1]<210 and array[y+dy,x+dx,2]<155
        for dx in (-16,16) for dy in range(-12,13,4))
    return 12<=yellow<=160 and outline>=4


def fit(image_path, cards, energy_types=()):
    im=np.asarray(Image.open(image_path).convert('RGB'),dtype=np.float32)
    # Energy cards have a green face, not a pale frame: register the board on
    # ordinary tiles, then require each exposed energy card to show a battery.
    energy=[c for c in exposed(cards) if c.get('metaType')==3 or c['type'] in energy_types]
    visible=[c for c in exposed(cards) if c not in energy]
    if len(visible)<4:raise ValueError('可见牌不足，无法自动对齐')
    col=np.array([c['rolNum'] for c in visible]);row=np.array([c['rowNum'] for c in visible])
    # Pale face probability. Icon pixels are excluded by sampling the frame.
    pale=(im[:,:,0]>205)&(im[:,:,1]>205)&(im[:,:,2]>145)
    dark=((im[:,:,0]<155)&(im[:,:,1]<195)&(im[:,:,2]<150)).astype(float)
    def scores(params,require_all=False):
        x0,y0,ux,uy=params.T
        xs=x0[:,None]+ux[:,None]*col;ys=y0[:,None]+uy[:,None]*row
        valid=(xs.min(axis=1)>=22)&(xs.max(axis=1)<=312)&(ys.min(axis=1)>=125)&(ys.max(axis=1)<=462)
        score=np.zeros(len(params));frame=np.zeros((len(params),len(visible)))
        sx=ux[:,None]/4.32;sy=uy[:,None]/4.25
        for dx,dy in FACE_OFFSETS:
            x=np.clip((np.rint(xs)+np.rint(dx*sx)).astype(int),0,335);y=np.clip((np.rint(ys)+np.rint(dy*sy)).astype(int),0,621)
            frame+=pale[y,x]
        score+=frame.mean(axis=1)
        for dx,dy in [(-16,-14),(16,-14),(-16,11),(16,11)]:
            x=np.clip(np.rint(xs+dx*sx).astype(int),0,335);y=np.clip(np.rint(ys+dy*sy).astype(int),0,621)
            score+=dark[y,x].mean(axis=1)*0.35
        # An average alone can accept a partly played board. Every exposed tile
        # must pass exactly the same frame gate used before real clicks.
        score[~valid]=-100
        if require_all:score[frame.min(axis=1)<5]=-100
        return score
    def result(best,score):
        rgb=im.astype(np.uint8)
        for c in energy:
            point=(int(round(best[0]+c['rolNum']*best[2])),int(round(best[1]+c['rowNum']*best[3])))
            if not (16<=point[0]<=319 and 13<=point[1]<=608) or not energy_face(rgb,point):
                raise ValueError(f'牌面几何未匹配：电池牌 {c["id"]} 位置没有电池牌面')
        calibration=dict(zip(('x_origin','y_origin','x_unit','y_unit'),map(float,best)))
        calibration.update(face_scale_x=float(best[2]/4.32),face_scale_y=float(best[3]/4.25))
        return calibration,round(float(score),3)
    # Direct registration has no fixed origin assumption and must match EVERY
    # exposed tile to its own frame. It also covers the smaller world tutorial.
    centers=_face_centers(pale);seed=_component_seed(col,row,centers)
    if seed is not None:
        score=scores(seed[None,:],require_all=True)[0]
        if score>=5.8:return result(seed,score)
    # Some icons split one frame into multiple pale components, while dense
    # edges hide other components. Match coordinates with outliers rather than
    # relying on component count or coordinate-descent initialization.
    if len(centers)>=4:
        params=np.array([(x0,y0,ux,uy) for x0,ux in _axis_seeds(col,centers[:,0])
                         for y0,uy in _axis_seeds(row,centers[:,1])])
        if len(params):
            values=scores(params,require_all=True);best=int(np.argmax(values))
            if values[best]>=5.8:
                chosen=params[best];coords=np.column_stack((col,row))
                points=chosen[:2]+coords*chosen[2:]
                distances=((points[:,None,:]-centers[None,:,:])**2).sum(axis=2)
                nearest=distances.argmin(axis=1);good=distances.min(axis=1)<36
                if good.sum()>=4 and len(set(nearest[good]))==good.sum() and all(np.ptp(coords[good,axis])>0 for axis in range(2)):
                    refined=chosen.copy()
                    for axis in range(2):
                        refined[axis],refined[axis+2]=np.linalg.lstsq(np.column_stack((np.ones(good.sum()),coords[good,axis])),centers[nearest[good],axis],rcond=None)[0]
                    refined_score=scores(refined[None,:],require_all=True)[0]
                    if refined_score>=5.8:return result(refined,refined_score)
                return result(chosen,values[best])
    # Fallback for icon fragments which split a pale frame into components.
    candidates=[]
    for start_y in range(120,181,8):
        best=np.array([47.5,float(start_y),4.32,4.25])
        for ranges in [(np.arange(12,73,2),np.arange(110,191,2),np.arange(3.5,4.91,.08),np.arange(3.5,4.81,.08)),
                       (None,None,None,None)]:
            for repeat in range(2):
                for axis in range(4):
                    values=ranges[axis] if ranges[axis] is not None else best[axis]+np.arange(-1.5,1.51,.25) if axis<2 else best[axis]+np.arange(-.08,.081,.01)
                    params=np.repeat(best[None,:],len(values),axis=0);params[:,axis]=values
                    best=params[int(np.argmax(scores(params)))]
        candidates.append((float(scores(best[None,:])[0]),best))
    score,best=max(candidates,key=lambda x:x[0])
    score=float(scores(best[None,:],require_all=True)[0])
    if score<5.8:raise ValueError(f'牌面几何未匹配：预期 {len(visible)} 张可点牌，识别到 {len(centers)} 个完整牌框，对齐分数 {score:.2f}/5.80')
    return result(best,score)


def mark(image_path, cards, calibration, output, next_id=None):
    im=Image.open(image_path).convert('RGB');d=ImageDraw.Draw(im)
    for c in exposed(cards):
        x=round(calibration['x_origin']+c['rolNum']*calibration['x_unit'])
        y=round(calibration['y_origin']+c['rowNum']*calibration['y_unit'])
        color='#e43c49' if c['id']==next_id else '#21926c'
        sx=calibration.get('face_scale_x',1);sy=calibration.get('face_scale_y',1)
        d.rectangle((x-round(16*sx),y-round(18*sy),x+round(16*sx),y+round(17*sy)),outline=color,width=2 if c['id']==next_id else 1)
    Path(output).parent.mkdir(parents=True,exist_ok=True);im.save(output)


def check_silhouette(image_path,cards,calibration):
    im=np.asarray(Image.open(image_path).convert('RGB'))
    pale=(im[:,:,0]>205)&(im[:,:,1]>205)&(im[:,:,2]>145)
    mask=np.zeros((622,336),dtype=bool)
    for c in cards:
        x=round(calibration['x_origin']+c['rolNum']*calibration['x_unit'])
        y=round(calibration['y_origin']+c['rowNum']*calibration['y_unit'])
        mask[max(118,y-23):min(466,y+23),max(20,x-21):min(316,x+21)]=True
    board=np.zeros_like(mask);board[118:466,20:316]=True
    total=np.count_nonzero(pale&board)
    outside=np.count_nonzero(pale&board&~mask)
    if total<100 or outside/max(1,total)>.12:
        raise ValueError('截图里有大量地图之外的牌；请确认当前关次，不要套用上一关地图')
