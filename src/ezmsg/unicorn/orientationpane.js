import * as THREE from "three"

export function render({ model, el }) {
    console.log(`${model.width} X ${model.height}, time = ${model.cur_time}`)

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera( 70, model.width / model.height, 0.01, 10 );
    camera.position.x = -0.3;
    camera.lookAt(new THREE.Vector3(0.0, 0.0, 0.0))
    const ref_quat = new THREE.Quaternion();
    ref_quat.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);

    console.log(ref_quat)

    const geometry = new THREE.BoxGeometry(0.2, 0.2, 0.2);
    const material = new THREE.MeshNormalMaterial();

    const mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);

    const renderer = new THREE.WebGLRenderer({
        antialias: true, 
        alpha: true
    });

    renderer.setSize(model.width, model.height);
    renderer.setClearColor(0xffffff, 0)
    el.append(renderer.domElement)

    model.on('orientation', () => {
        let quat = new THREE.Quaternion(
            model.orientation[0],
            model.orientation[1],
            model.orientation[2],
            model.orientation[3]
        );
        mesh.rotation.setFromQuaternion(quat.multiply(ref_quat));
        renderer.render(scene, camera);
    })

    renderer.render(scene, camera);
}