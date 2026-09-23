import { PETS } from './choreography';

export default function PetAvatar({ petIndex }: { petIndex: number }) {
  const pet = PETS[petIndex];
  return <svg className="pet-avatar" viewBox="0 0 88 92" fill="none" shapeRendering="crispEdges" aria-hidden="true">
    <path className="pet-shadow" d="M20 84h48v4H20z" fill="#17282d" opacity=".2" />
    <g className="pet-foot pet-foot-left"><path d="M28 72h12v12H24v-4h4z" fill="#26343c" /><path d="M28 72h8v4h-8z" fill={pet.dark} /></g>
    <g className="pet-foot pet-foot-right"><path d="M48 72h12v8h4v4H48z" fill="#26343c" /><path d="M52 72h8v4h-8z" fill={pet.dark} /></g>
    <g className="pet-body">
      <g className="pet-arm pet-arm-left"><path d="M16 52h12v20H16z" fill="#26343c" /><path d="M20 56h8v12h-8z" fill={pet.color} /><path d="M20 64h8v4h-8z" fill="#fff1c6" /></g>
      <g className="pet-arm pet-arm-right"><path d="M60 52h12v20H60z" fill="#26343c" /><path d="M60 56h8v12h-8z" fill={pet.color} /><path d="M60 64h8v4h-8z" fill="#fff1c6" /></g>
      <path d="M24 48h40v28H24z" fill="#26343c" /><path d="M28 52h32v20H28z" fill={pet.color} /><path d="M28 52h4v16h-4z" fill={pet.light} /><path d="M32 64h24v4H32z" fill="#fff1c6" />
      <path d="M36 52h16v8H36z" fill={pet.dark} /><path d="M40 52h8v8h-8z" fill="#fff1c6" />
      <path d="M24 16h40v8h8v24h-8v8H24v-8h-8V24h8z" fill="#26343c" />
      <path d="M24 20h40v8h4v16h-4v8H24v-8h-4V28h4z" fill={pet.color} />
      <path d="M28 28h32v20H28z" fill="#fff1c6" /><path d="M28 28h32v4H28z" fill="#fffbe3" />
      <path d="M24 20h36v4H24z" fill={pet.light} /><path d="M60 28h4v20h-4z" fill={pet.dark} />
      <g className="pet-eyes" fill="#26343c"><path d="M32 34h4v8h-4zM52 34h4v8h-4z" /></g>
      <path d="M40 44h8v4h-8z" fill="#b87969" />
      {pet.kind === 'courier' && <><path d="M28 8h28v8H28zM24 12h36v8H24z" fill="#26343c" /><path d="M28 12h28v4H28z" fill={pet.color} /><path d="M24 20h28v4H24z" fill={pet.light} /></>}
      {pet.kind === 'analyst' && <><path d="M28 8h24v8H28zM48 12h12v8H48z" fill={pet.dark} /><path d="M28 32h12v12H28zM48 32h12v12H48z" stroke="#26343c" strokeWidth="3" /><path d="M40 36h8v4h-8z" fill="#26343c" /></>}
      {pet.kind === 'caller' && <><path d="M32 8h8v8h-8zM48 4h8v12h-8z" fill={pet.color} /><path d="M20 20h4v24h-8V28h4zM64 20h4v8h4v16h-8zM64 44h4v8H52v-4h12z" fill="#655079" /><path d="M16 32h4v8h-4zM68 32h4v8h-4z" fill={pet.light} /></>}
      {pet.kind === 'captain' && <><path d="M28 8h8v8h4V4h8v12h4V8h8v16H28z" fill={pet.color} /><path d="M28 8h4v12h-4zM40 4h4v16h-4z" fill={pet.light} /><path d="M28 24h32v4H28z" fill={pet.dark} /></>}
    </g>
  </svg>;
}
